from datetime import timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from fastapi.responses import JSONResponse
from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_serializer,
    field_validator,
)

from .auth import UserResponse, get_current_user, require_trusted_origin
from .config import get_moderation_settings
from .database import get_connection
from .moderation import ModerationError, moderate_text

router = APIRouter(prefix="/api/posts", tags=["Posty"])
CurrentUser = Annotated[UserResponse, Depends(get_current_user)]
RecordId = Annotated[int, Path(ge=1, le=2147483647)]
PageLimit = Annotated[int, Query(ge=1, le=50)]
PageOffset = Annotated[int, Query(ge=0, le=9223372036854775807)]
MODERATION_REJECTION_MESSAGES = {
    "review": "Ten wpis może wymagać zmian. Popraw treść i spróbuj ponownie.",
    "unsafe": "Ten wpis może naruszać zasady społeczności i nie został opublikowany.",
}

class ContentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=280)

    @field_validator("content", mode="before")
    @classmethod
    def trim_content(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("content")
    @classmethod
    def reject_invalid_characters(cls, value):
        if "\x00" in value or any(0xD800 <= ord(char) <= 0xDFFF for char in value):
            raise ValueError("Tekst zawiera niedozwolone znaki.")
        return value

class PostInput(ContentInput):
    is_anonymous: bool = Field(default=False, strict=True)

class PostAuthor(BaseModel):
    id: int
    username: str

class PublishedContent(BaseModel):
    id: int
    content: str
    created_at: AwareDatetime
    is_owner: bool

    @field_serializer("created_at")
    def serialize_date(self, value):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

class PostResponse(PublishedContent):
    is_anonymous: bool
    author: PostAuthor | None
    likes_count: int = Field(default=0, ge=0)
    liked_by_me: bool = False
    replies_count: int = Field(default=0, ge=0)

class ReplyResponse(PublishedContent):
    post_id: int
    author: PostAuthor

class RepliesPage(BaseModel):
    items: list[ReplyResponse]
    total: int

def post_from_row(row, current_user_id: int) -> PostResponse:
    return PostResponse(
        id=row["id"],
        content=row["content"],
        created_at=row["created_at"],
        is_anonymous=row["is_anonymous"],
        author=None
        if row["is_anonymous"]
        else PostAuthor(id=row["user_id"], username=row["username"]),
        is_owner=row["user_id"] == current_user_id,
        likes_count=row.get("likes_count", 0),
        liked_by_me=row.get("liked_by_me", False),
        replies_count=row.get("replies_count", 0),
    )

@router.get("", response_model=list[PostResponse])
def list_posts(
    user: CurrentUser,
    limit: PageLimit = 20,
    offset: PageOffset = 0,
) -> list[PostResponse]:
    with get_connection() as connection:
        rows = connection.execute(
            "WITH page AS ("
            "SELECT id, user_id, content, created_at, is_anonymous FROM posts "
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
            ") "
            "SELECT p.id, p.user_id, p.content, p.created_at, p.is_anonymous, u.username, "
            "COUNT(l.id) AS likes_count, COALESCE(BOOL_OR(l.user_id = %s), FALSE) AS liked_by_me, "
            "(SELECT COUNT(*) FROM replies AS r WHERE r.post_id = p.id) AS replies_count "
            "FROM page AS p JOIN users AS u ON u.id = p.user_id "
            "LEFT JOIN likes AS l ON l.post_id = p.id "
            "GROUP BY p.id, p.user_id, p.content, p.created_at, p.is_anonymous, u.id "
            "ORDER BY p.created_at DESC, p.id DESC",
            (limit, offset, user.id),
        ).fetchall()
    return [post_from_row(row, user.id) for row in rows]

@router.get("/trending", response_model=list[PostResponse])
def trending_posts(user: CurrentUser) -> list[PostResponse]:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT p.id, p.user_id, p.content, p.created_at, p.is_anonymous, u.username,
                   COUNT(l.id) AS likes_count,
                   COALESCE(BOOL_OR(l.user_id = %s), FALSE) AS liked_by_me,
                   (SELECT COUNT(*) FROM replies AS r WHERE r.post_id = p.id) AS replies_count
            FROM posts AS p
            JOIN users AS u ON u.id = p.user_id
            LEFT JOIN likes AS l ON l.post_id = p.id
            WHERE p.created_at BETWEEN NOW() - INTERVAL '24 hours' AND NOW()
            GROUP BY p.id, u.id
            ORDER BY likes_count DESC, p.created_at DESC, p.id DESC
            LIMIT 3
            """,
            (user.id,),
        ).fetchall()
    return [post_from_row(row, user.id) for row in rows]

def moderation_rejection(content: str) -> JSONResponse | None:
    if get_moderation_settings().enabled:
        try:
            decision = moderate_text(content).decision
        except ModerationError:
            raise HTTPException(
                503, "Moderacja jest chwilowo niedostępna. Spróbuj ponownie później."
            ) from None
        if decision != "safe":
            return JSONResponse(
                status_code=422,
                content={
                    "message": MODERATION_REJECTION_MESSAGES[decision],
                    "moderation_decision": decision,
                },
            )
    return None

@router.post(
    "",
    status_code=201,
    response_model=PostResponse,
    dependencies=[Depends(require_trusted_origin)],
)
def create_post(data: PostInput, user: CurrentUser) -> PostResponse | JSONResponse:
    if rejection := moderation_rejection(data.content):
        return rejection
    with get_connection() as connection:
        row = connection.execute(
            "INSERT INTO posts (user_id, content, is_anonymous) VALUES (%s, %s, %s) "
            "RETURNING id, user_id, content, created_at, is_anonymous",
            (user.id, data.content, data.is_anonymous),
        ).fetchone()
    return post_from_row(row | {"username": user.username}, user.id)

def reply_from_row(row, current_user_id: int) -> ReplyResponse:
    return ReplyResponse(
        id=row["id"],
        post_id=row["post_id"],
        content=row["content"],
        created_at=row["created_at"],
        author=PostAuthor(id=row["user_id"], username=row["username"]),
        is_owner=row["user_id"] == current_user_id,
    )

@router.get("/{post_id}/replies", response_model=RepliesPage)
def list_replies(
    post_id: RecordId, user: CurrentUser, limit: PageLimit = 20, offset: PageOffset = 0
) -> RepliesPage:
    with get_connection() as connection:
        rows = connection.execute(
            """
            SELECT p.id AS post_id, page.*,
                   (SELECT COUNT(*) FROM replies WHERE post_id = p.id) AS total
            FROM posts AS p
            LEFT JOIN LATERAL (
                SELECT r.id, r.user_id, r.content, r.created_at, u.username
                FROM replies AS r JOIN users AS u ON u.id = r.user_id
                WHERE r.post_id = p.id
                ORDER BY r.created_at, r.id
                LIMIT %s OFFSET %s
            ) AS page ON TRUE
            WHERE p.id = %s
            ORDER BY page.created_at, page.id
            """,
            (limit, offset, post_id),
        ).fetchall()
    if not rows:
        raise HTTPException(404, "Post nie istnieje.")
    return RepliesPage(
        items=[reply_from_row(row, user.id) for row in rows if row["id"] is not None],
        total=rows[0]["total"],
    )

@router.post(
    "/{post_id}/replies",
    status_code=201,
    response_model=ReplyResponse,
    dependencies=[Depends(require_trusted_origin)],
)
def create_reply(post_id: RecordId, data: ContentInput, user: CurrentUser) -> ReplyResponse | JSONResponse:
    with get_connection() as connection:
        if connection.execute("SELECT 1 FROM posts WHERE id = %s", (post_id,)).fetchone() is None:
            raise HTTPException(404, "Post nie istnieje.")
    if rejection := moderation_rejection(data.content):
        return rejection
    with get_connection() as connection:
        row = connection.execute(
            "INSERT INTO replies (post_id, user_id, content) "
            "SELECT id, %s, %s FROM posts WHERE id = %s FOR KEY SHARE "
            "RETURNING id, post_id, user_id, content, created_at",
            (user.id, data.content, post_id),
        ).fetchone()
    if row is None:
        raise HTTPException(404, "Post nie istnieje.")
    return reply_from_row(row | {"username": user.username}, user.id)

@router.delete(
    "/{post_id}/replies/{reply_id}",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_trusted_origin)],
)
def delete_reply(post_id: RecordId, reply_id: RecordId, user: CurrentUser) -> Response:
    with get_connection() as connection:
        reply = connection.execute(
            "SELECT user_id FROM replies WHERE id = %s AND post_id = %s FOR UPDATE",
            (reply_id, post_id),
        ).fetchone()
        if reply is None:
            raise HTTPException(404, "Odpowiedź nie istnieje.")
        if reply["user_id"] != user.id:
            raise HTTPException(403, "Możesz usuwać tylko własne odpowiedzi.")
        connection.execute(
            "DELETE FROM replies WHERE id = %s AND post_id = %s AND user_id = %s",
            (reply_id, post_id, user.id),
        )
    return Response(status_code=204)

class PostLikeResponse(BaseModel):
    likes_count: int = Field(ge=0)
    liked_by_me: bool

def set_post_like(post_id: int, user_id: int, liked: bool) -> PostLikeResponse:
    with get_connection() as connection:
        post = connection.execute(
            "SELECT id FROM posts WHERE id = %s FOR UPDATE", (post_id,)
        ).fetchone()
        if post is None:
            raise HTTPException(404, "Post nie istnieje.")
        if liked:
            connection.execute(
                "INSERT INTO likes (user_id, post_id) VALUES (%s, %s) "
                "ON CONFLICT (user_id, post_id) DO NOTHING",
                (user_id, post_id),
            )
        else:
            connection.execute(
                "DELETE FROM likes WHERE user_id = %s AND post_id = %s",
                (user_id, post_id),
            )
        count = connection.execute(
            "SELECT COUNT(*) AS likes_count FROM likes WHERE post_id = %s", (post_id,)
        ).fetchone()["likes_count"]
    return PostLikeResponse(likes_count=count, liked_by_me=liked)

@router.post(
    "/{post_id}/like",
    response_model=PostLikeResponse,
    dependencies=[Depends(require_trusted_origin)],
)
def like_post(post_id: RecordId, user: CurrentUser) -> PostLikeResponse:
    return set_post_like(post_id, user.id, True)

@router.delete(
    "/{post_id}/like",
    response_model=PostLikeResponse,
    dependencies=[Depends(require_trusted_origin)],
)
def unlike_post(post_id: RecordId, user: CurrentUser) -> PostLikeResponse:
    return set_post_like(post_id, user.id, False)

@router.delete(
    "/{post_id}",
    status_code=204,
    response_class=Response,
    dependencies=[Depends(require_trusted_origin)],
)
def delete_post(post_id: RecordId, user: CurrentUser) -> Response:
    with get_connection() as connection:
        post = connection.execute(
            "SELECT user_id FROM posts WHERE id = %s FOR UPDATE", (post_id,)
        ).fetchone()
        if post is None:
            raise HTTPException(404, "Post nie istnieje.")
        if post["user_id"] != user.id:
            raise HTTPException(403, "Możesz usuwać tylko własne posty.")
        connection.execute(
            "DELETE FROM posts WHERE id = %s AND user_id = %s", (post_id, user.id)
        )
    return Response(status_code=204)
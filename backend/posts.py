from datetime import timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_serializer, field_validator
from .auth import UserResponse, get_current_user, require_trusted_origin
from .database import get_connection
router = APIRouter(prefix="/api/posts", tags=["Posty"])
CurrentUser = Annotated[UserResponse, Depends(get_current_user)]
PostId = Annotated[int, Path(ge=1, le=2147483647)]

class PostInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    content: str = Field(min_length=1, max_length=280)
    is_anonymous: bool = Field(default=False, strict=True)

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

class PostAuthor(BaseModel):
    id: int
    username: str

class PostResponse(BaseModel):
    id: int
    content: str
    created_at: AwareDatetime
    is_anonymous: bool
    author: PostAuthor | None
    is_owner: bool
    likes_count: int = Field(default=0, ge=0)
    liked_by_me: bool = False

    @field_serializer("created_at")
    def serialize_date(self, value):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

def post_from_row(row, current_user_id: int) -> PostResponse:
    return PostResponse(
        id=row["id"], content=row["content"], created_at=row["created_at"],
        is_anonymous=row["is_anonymous"],
        author=None if row["is_anonymous"] else PostAuthor(id=row["user_id"], username=row["username"]),
        is_owner=row["user_id"] == current_user_id,
        likes_count=row.get("likes_count", 0), liked_by_me=row.get("liked_by_me", False),
    )

@router.get("", response_model=list[PostResponse])
def list_posts(
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
) -> list[PostResponse]:
    with get_connection() as connection:
        rows = connection.execute(
            "WITH page AS ("
            "SELECT id, user_id, content, created_at, is_anonymous FROM posts "
            "ORDER BY created_at DESC, id DESC LIMIT %s OFFSET %s"
            ") "
            "SELECT p.id, p.user_id, p.content, p.created_at, p.is_anonymous, u.username, "
            "COUNT(l.id) AS likes_count, COALESCE(BOOL_OR(l.user_id = %s), FALSE) AS liked_by_me "
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
                   COALESCE(BOOL_OR(l.user_id = %s), FALSE) AS liked_by_me
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

@router.post("", status_code=201, response_model=PostResponse, dependencies=[Depends(require_trusted_origin)])
def create_post(data: PostInput, user: CurrentUser) -> PostResponse:
    with get_connection() as connection:
        row = connection.execute(
            "INSERT INTO posts (user_id, content, is_anonymous) VALUES (%s, %s, %s) "
            "RETURNING id, user_id, content, created_at, is_anonymous",
            (user.id, data.content, data.is_anonymous),
        ).fetchone()
    return post_from_row(row | {"username": user.username}, user.id)

class PostLikeResponse(BaseModel):
    likes_count: int = Field(ge=0)
    liked_by_me: bool

def set_post_like(post_id: int, user_id: int, liked: bool) -> PostLikeResponse:
    with get_connection() as connection:
        post = connection.execute("SELECT id FROM posts WHERE id = %s FOR UPDATE", (post_id,)).fetchone()
        if post is None:
            raise HTTPException(404, "Post nie istnieje.")
        if liked:
            connection.execute(
                "INSERT INTO likes (user_id, post_id) VALUES (%s, %s) "
                "ON CONFLICT (user_id, post_id) DO NOTHING",
                (user_id, post_id),
            )
        else:
            connection.execute("DELETE FROM likes WHERE user_id = %s AND post_id = %s", (user_id, post_id))
        count = connection.execute(
            "SELECT COUNT(*) AS likes_count FROM likes WHERE post_id = %s", (post_id,)
        ).fetchone()["likes_count"]
    return PostLikeResponse(likes_count=count, liked_by_me=liked)

@router.post("/{post_id}/like", response_model=PostLikeResponse, dependencies=[Depends(require_trusted_origin)])
def like_post(post_id: PostId, user: CurrentUser) -> PostLikeResponse:
    return set_post_like(post_id, user.id, True)


@router.delete("/{post_id}/like", response_model=PostLikeResponse, dependencies=[Depends(require_trusted_origin)])
def unlike_post(post_id: PostId, user: CurrentUser) -> PostLikeResponse:
    return set_post_like(post_id, user.id, False)

@router.delete("/{post_id}", status_code=204, response_class=Response, dependencies=[Depends(require_trusted_origin)])
def delete_post(post_id: PostId, user: CurrentUser) -> Response:
    with get_connection() as connection:
        post = connection.execute(
            "SELECT user_id FROM posts WHERE id = %s FOR UPDATE", (post_id,)
        ).fetchone()
        if post is None:
            raise HTTPException(404, "Post nie istnieje.")
        if post["user_id"] != user.id:
            raise HTTPException(403, "Możesz usuwać tylko własne posty.")
        connection.execute("DELETE FROM posts WHERE id = %s AND user_id = %s", (post_id, user.id))
    return Response(status_code=204)
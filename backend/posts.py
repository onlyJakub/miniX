from datetime import timezone
from typing import Annotated, Literal
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
    author: PostAuthor
    likes_count: Literal[0] = 0

    @field_serializer("created_at")
    def serialize_date(self, value):
        return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")

@router.get("", response_model=list[PostResponse])
def list_posts(
    user: CurrentUser,
    limit: Annotated[int, Query(ge=1, le=50)] = 20,
    offset: Annotated[int, Query(ge=0, le=9223372036854775807)] = 0,
) -> list[PostResponse]:
    with get_connection() as connection:
        rows = connection.execute(
            "SELECT p.id, p.content, p.created_at, u.id AS author_id, u.username "
            "FROM posts AS p JOIN users AS u ON u.id = p.user_id "
            "ORDER BY p.created_at DESC, p.id DESC LIMIT %s OFFSET %s",
            (limit, offset),
        ).fetchall()
    return [
        PostResponse(
            id=row["id"], content=row["content"], created_at=row["created_at"],
            author=PostAuthor(id=row["author_id"], username=row["username"]),
        )
        for row in rows
    ]

@router.post("", status_code=201, response_model=PostResponse, dependencies=[Depends(require_trusted_origin)])
def create_post(data: PostInput, user: CurrentUser) -> PostResponse:
    with get_connection() as connection:
        row = connection.execute(
            "INSERT INTO posts (user_id, content) VALUES (%s, %s) RETURNING id, content, created_at",
            (user.id, data.content),
        ).fetchone()
    return PostResponse(**row, author=PostAuthor(id=user.id, username=user.username))

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
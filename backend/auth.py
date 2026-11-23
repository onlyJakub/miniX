import secrets
from datetime import datetime, timedelta, timezone
from typing import Annotated
import jwt
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError
from fastapi import APIRouter, Depends, HTTPException, Request, Response
from psycopg.errors import UniqueViolation
from pydantic import BaseModel, ConfigDict, EmailStr, Field, SecretStr, field_validator
from .config import AuthSettings, get_auth_settings
from .database import get_connection

COOKIE_NAME = "minix_session"
COOKIE_PATH = "/api"
password_hasher = PasswordHasher()
dummy_password_hash = password_hasher.hash(secrets.token_urlsafe(32))

class RegisterInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    username: str = Field(min_length=3, max_length=50, pattern=r"^[A-Za-z0-9_]+$")
    email: EmailStr = Field(max_length=255)
    password: SecretStr

    @field_validator("username", "email", mode="before")
    @classmethod
    def trim_identity(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def normalize_email(cls, value):
        return str(value).lower()

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        password = value.get_secret_value()
        if not 8 <= len(password) <= 128 or not password.strip():
            raise ValueError(
                "Hasło musi zawierać od 8 do 128 znaków i nie może być samymi białymi znakami."
            )
        return value

class LoginInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    identifier: str = Field(min_length=1, max_length=255)
    password: SecretStr

    @field_validator("identifier", mode="before")
    @classmethod
    def trim_identifier(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("password")
    @classmethod
    def validate_password(cls, value):
        if not 1 <= len(value.get_secret_value()) <= 128:
            raise ValueError("Podaj hasło o długości do 128 znaków.")
        return value

class UserResponse(BaseModel):
    id: int
    username: str
    email: str

class MessageResponse(BaseModel):
    message: str

def require_trusted_origin(
    request: Request, settings: Annotated[AuthSettings, Depends(get_auth_settings)]
) -> None:
    """ochrona przed CSRF"""
    if request.headers.get("origin") not in settings.allowed_origins:
        raise HTTPException(403, "Niedozwolone źródło żądania (Origin).")

router = APIRouter(prefix="/api/auth", tags=["Uwierzytelnianie"])
mutation_dependencies = [Depends(require_trusted_origin)]

def create_token(user_id: int, settings: AuthSettings) -> str:
    now = datetime.now(timezone.utc)
    return jwt.encode(
        {
            "sub": str(user_id),
            "iat": now,
            "exp": now + timedelta(seconds=settings.lifetime_seconds),
        },
        settings.jwt_secret,
        algorithm="HS256",
    )

def get_current_user(
    request: Request, settings: Annotated[AuthSettings, Depends(get_auth_settings)]
) -> UserResponse:
    token = request.cookies.get(COOKIE_NAME)
    unauthorized = HTTPException(401, "Zaloguj się, aby kontynuować.")
    if not token:
        raise unauthorized

    try:
        claims = jwt.decode(
            token,
            settings.jwt_secret,
            algorithms=["HS256"],
            options={"require": ["sub", "iat", "exp"]},
        )
        subject = claims["sub"]
        if (
            not isinstance(subject, str)
            or not subject.isascii()
            or not subject.isdecimal()
        ):
            raise unauthorized
        user_id = int(subject)
        if not 1 <= user_id <= 2147483647:
            raise unauthorized
    except (jwt.InvalidTokenError, ValueError):
        raise unauthorized from None

    with get_connection() as connection:
        user = connection.execute(
            "SELECT id, username, email FROM users WHERE id = %s", (user_id,)
        ).fetchone()
    if not user:
        raise unauthorized
    return UserResponse(**user)

@router.post(
    "/register",
    status_code=201,
    response_model=MessageResponse,
    dependencies=mutation_dependencies,
)
def register(data: RegisterInput) -> MessageResponse:
    password_hash = password_hasher.hash(data.password.get_secret_value())
    try:
        with get_connection() as connection:
            connection.execute(
                "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s)",
                (data.username, data.email, password_hash),
            )
    except UniqueViolation as error:
        constraint = error.diag.constraint_name or ""
        if "username" in constraint:
            message = "Ta nazwa użytkownika jest już zajęta."
        elif "email" in constraint:
            message = "Ten adres e-mail jest już zajęty."
        else:
            message = "Nazwa użytkownika lub adres e-mail są już zajęte."
        raise HTTPException(409, message) from None
    return MessageResponse(message="Konto zostało utworzone. Możesz się zalogować.")

@router.post("/login", response_model=UserResponse, dependencies=mutation_dependencies)
def login(
    data: LoginInput,
    response: Response,
    settings: Annotated[AuthSettings, Depends(get_auth_settings)],
) -> UserResponse:
    with get_connection() as connection:
        user = connection.execute(
            "SELECT id, username, email, password_hash FROM users "
            "WHERE lower(username) = lower(%s) OR lower(email) = lower(%s)",
            (data.identifier, data.identifier),
        ).fetchone()

    stored_hash = user["password_hash"] if user else dummy_password_hash
    try:
        password_hasher.verify(stored_hash, data.password.get_secret_value())
    except (VerificationError, InvalidHashError):
        raise HTTPException(401, "Nieprawidłowy login lub hasło.") from None
    if user is None:
        raise HTTPException(401, "Nieprawidłowy login lub hasło.")

    if password_hasher.check_needs_rehash(stored_hash):
        new_hash = password_hasher.hash(data.password.get_secret_value())
        with get_connection() as connection:
            connection.execute(
                "UPDATE users SET password_hash = %s WHERE id = %s AND password_hash = %s",
                (new_hash, user["id"], stored_hash),
            )

    response.set_cookie(
        COOKIE_NAME,
        create_token(user["id"], settings),
        max_age=settings.lifetime_seconds,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return UserResponse(id=user["id"], username=user["username"], email=user["email"])

@router.post(
    "/logout", response_model=MessageResponse, dependencies=mutation_dependencies
)
def logout(
    response: Response, settings: Annotated[AuthSettings, Depends(get_auth_settings)]
) -> MessageResponse:
    response.delete_cookie(
        COOKIE_NAME,
        path=COOKIE_PATH,
        httponly=True,
        secure=settings.cookie_secure,
        samesite="lax",
    )
    return MessageResponse(message="Wylogowano pomyślnie.")

@router.get("/me", response_model=UserResponse)
def me(user: Annotated[UserResponse, Depends(get_current_user)]) -> UserResponse:
    return user
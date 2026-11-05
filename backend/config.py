"""Ustawienia uwierzytelniania"""
import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import urlsplit

from dotenv import load_dotenv

load_dotenv(Path(__file__).with_name(".env"))

@dataclass(frozen=True)
class AuthSettings:
    jwt_secret: str
    cookie_secure: bool
    allowed_origins: tuple[str, ...]
    lifetime_seconds: int

@lru_cache
def get_auth_settings() -> AuthSettings:
    secret = os.getenv("JWT_SECRET", "")
    if len(secret.encode("utf-8")) < 32 or secret.startswith("CHANGE_ME"):
        raise RuntimeError("Ustaw losowy JWT_SECRET (minimum 32 bajty) w backend/.env.")

    secure_value = os.getenv("COOKIE_SECURE", "true").lower()
    if secure_value not in {"true", "false"}:
        raise RuntimeError("COOKIE_SECURE musi mieć wartość true lub false.")

    origins = tuple(
        value.strip() for value in os.getenv(
            "AUTH_ORIGINS", "http://127.0.0.1:5173,http://localhost:5173,http://127.0.0.1:8000"
        ).split(",") if value.strip()
    )
    if not origins:
        raise RuntimeError("AUTH_ORIGINS musi zawierać przynajmniej jeden adres aplikacji.")
    for origin in origins:
        parsed = urlsplit(origin)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc or parsed.path or parsed.query or parsed.fragment or parsed.username:
            raise RuntimeError("AUTH_ORIGINS musi zawierać pełne originy bez ścieżek i końcowego ukośnika.")

    try:
        minutes = int(os.getenv("JWT_EXPIRE_MINUTES", "60"))
    except ValueError:
        raise RuntimeError("JWT_EXPIRE_MINUTES musi być liczbą całkowitą.") from None
    if not 1 <= minutes <= 1440:
        raise RuntimeError("JWT_EXPIRE_MINUTES musi być w zakresie 1–1440.")

    return AuthSettings(secret, secure_value == "true", origins, minutes * 60)
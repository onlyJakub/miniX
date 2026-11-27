from contextlib import asynccontextmanager
from typing import Literal
import psycopg
from fastapi import FastAPI, Request, Response, status
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from starlette.exceptions import HTTPException as StarletteHTTPException
from .auth import router as auth_router
from .config import get_auth_settings
from .database import DatabaseNotConfiguredError, get_connection
from .posts import router as posts_router

@asynccontextmanager
async def lifespan(app: FastAPI):
    get_auth_settings()
    yield

app = FastAPI(
    title="MiniX API",
    version="0.5.0-beta",
    lifespan=lifespan,
)
app.include_router(auth_router)
app.include_router(posts_router)

@app.middleware("http")
async def no_cache_auth(request: Request, call_next):
    response = await call_next(request)
    if request.url.path.startswith(("/api/auth/", "/api/posts")):
        response.headers["Cache-Control"] = "no-store"
    return response

@app.exception_handler(StarletteHTTPException)
async def http_error(request: Request, error: StarletteHTTPException):
    return JSONResponse(
        {"message": str(error.detail)},
        status_code=error.status_code,
        headers=error.headers,
    )

@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, error: RequestValidationError):
    messages = {
        "username": "Podaj 3–50 liter A–Z, cyfr lub znaków podkreślenia.",
        "email": "Podaj poprawny adres e-mail (maksymalnie 255 znaków).",
        "password": "Podaj prawidłowe hasło (rejestracja: 8–128 znaków).",
        "identifier": "Podaj login lub e-mail (maksymalnie 255 znaków).",
        "content": "Podaj niepusty tekst do 280 znaków, bez niedozwolonych znaków sterujących.",
        "is_anonymous": "Wybierz true lub false dla publikowania anonimowego.",
        "limit": "Limit musi być liczbą całkowitą od 1 do 50.",
        "offset": "Offset musi być nieujemną liczbą całkowitą mieszczącą się w zakresie bigint.",
        "post_id": "Identyfikator posta musi być dodatnią liczbą całkowitą z zakresu INTEGER.",
        "reply_id": "Identyfikator odpowiedzi musi być dodatnią liczbą całkowitą z zakresu INTEGER.",
    }
    errors = []
    for item in error.errors():
        field = str(item["loc"][-1])
        errors.append(
            {
                "field": field if field in messages else "form",
                "message": messages.get(field, "Nieprawidłowe dane żądania."),
            }
        )
    return JSONResponse(
        {"message": "Sprawdź dane formularza.", "errors": errors}, status_code=422
    )

@app.exception_handler(DatabaseNotConfiguredError)
async def database_not_configured(request: Request, error: DatabaseNotConfiguredError):
    return JSONResponse(
        {"message": "Baza danych nie jest skonfigurowana."}, status_code=503
    )

@app.exception_handler(psycopg.Error)
async def database_error(request: Request, error: psycopg.Error):
    return JSONResponse(
        {"message": "Baza danych jest niedostępna lub wymaga przygotowania schematu."},
        status_code=503,
    )

class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["connected", "unavailable", "not_configured"]
    message: str

@app.get(
    "/api/health",
    response_model=HealthResponse,
    tags=["Diagnostyka"],
    responses={
        503: {
            "model": HealthResponse,
            "description": "Baza danych jest niedostępna lub nieskonfigurowana.",
        }
    },
)

def health(response: Response) -> HealthResponse:
    response.headers["Cache-Control"] = "no-store"

    try:
        with get_connection() as connection:
            connection.execute("SELECT 1").fetchone()
    except DatabaseNotConfiguredError:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="error",
            database="not_configured",
            message="API działa. Uzupełnij DATABASE_URL w pliku backend/.env.",
        )
    except psycopg.Error:
        response.status_code = status.HTTP_503_SERVICE_UNAVAILABLE
        return HealthResponse(
            status="error",
            database="unavailable",
            message="API działa, ale baza danych jest niedostępna. Sprawdź PostgreSQL i DATABASE_URL.",
        )

    return HealthResponse(
        status="ok",
        database="connected",
        message="Połączenie działa: frontend → API → PostgreSQL.",
    )
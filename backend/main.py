from typing import Literal

import psycopg
from fastapi import FastAPI, Response, status
from pydantic import BaseModel

from .database import DatabaseNotConfiguredError, get_connection

app = FastAPI(
    title="MiniX API",
    version="0.1.0",
)

class HealthResponse(BaseModel):
    status: Literal["ok", "error"]
    database: Literal["connected", "unavailable", "not_configured"]
    message: str

@app.get(
    "/api/health",
    response_model=HealthResponse,
    tags=["Diagnostyka"],
    responses={503: {"model": HealthResponse, "description": "Baza danych jest niedostępna lub nieskonfigurowana."}},
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
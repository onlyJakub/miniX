import os
import psycopg
from psycopg.rows import dict_row
from . import config

class DatabaseNotConfiguredError(Exception):
    """Brak adresu bazy danych w konfiguracji."""

def get_connection():
    database_url = os.getenv("DATABASE_URL", "").strip()

    if not database_url:
        raise DatabaseNotConfiguredError

    return psycopg.connect(
        database_url,
        connect_timeout=3,
        options="-c statement_timeout=3000",
        row_factory=dict_row,
    )
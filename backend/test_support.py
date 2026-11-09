import os
import secrets
import unittest
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch
from uuid import uuid4

import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from psycopg.rows import dict_row

from backend.config import get_auth_settings
from backend.main import app

ROOT = Path(__file__).resolve().parents[1]
ORIGIN = "http://127.0.0.1:5173"
TEST_DATABASE_URL = os.getenv("TEST_DATABASE_URL", "")


@unittest.skipUnless(TEST_DATABASE_URL, "Ustaw TEST_DATABASE_URL do odrębnej bazy PostgreSQL z prawem CREATE SCHEMA.")
class DatabaseTestCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.stack = ExitStack()
        cls.addClassCleanup(cls.stack.close)
        cls.stack.enter_context(patch.dict(os.environ, {
            "JWT_SECRET": secrets.token_urlsafe(48), "COOKIE_SECURE": "false",
            "AUTH_ORIGINS": ORIGIN, "JWT_EXPIRE_MINUTES": "60",
        }))
        get_auth_settings.cache_clear()
        cls.stack.callback(get_auth_settings.cache_clear)
        cls.schema_name = "minix_test_" + uuid4().hex
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=3) as connection:
            connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(cls.schema_name)))
        cls.stack.callback(cls.cleanup_schema)
        with cls.connect() as connection:
            connection.execute((ROOT / "schema.sql").read_text(encoding="utf-8"))
        for module in ("auth", "posts", "main"):
            cls.stack.enter_context(patch(f"backend.{module}.get_connection", side_effect=cls.connect))
        cls.client = cls.stack.enter_context(TestClient(app, base_url="http://testserver", headers={"Origin": ORIGIN}))

    @classmethod
    def connect(cls):
        return psycopg.connect(
            TEST_DATABASE_URL, row_factory=dict_row, connect_timeout=3,
            options=f"-c search_path={cls.schema_name} -c statement_timeout=5000",
        )

    @classmethod
    def cleanup_schema(cls):
        with psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=3) as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(cls.schema_name)))

    def setUp(self):
        self.client.cookies.clear()
        suffix = uuid4().hex[:12]
        self.account = {"username": "user_" + suffix, "email": suffix + "@example.com", "password": "Test_password_123!"}

    def register(self, **changes):
        return self.client.post("/api/auth/register", json=self.account | changes)

    def login(self, **changes):
        return self.client.post("/api/auth/login", json={"identifier": self.account["username"], "password": self.account["password"]} | changes)

    def create_and_login(self):
        self.assertEqual(self.register().status_code, 201)
        response = self.login()
        self.assertEqual(response.status_code, 200)
        return response
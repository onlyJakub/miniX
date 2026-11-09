"""Testy integracyjne"""
import os
import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
import jwt
import psycopg
from fastapi.testclient import TestClient
from psycopg import sql
from backend.auth import COOKIE_NAME, COOKIE_PATH, password_hasher
from backend.config import get_auth_settings
from backend.main import app

from backend.test_support import DatabaseTestCase, ORIGIN, ROOT, TEST_DATABASE_URL

class AuthenticationTests(DatabaseTestCase):

    def test_register_stores_only_argon2id_hash(self):
        response = self.register()
        self.assertEqual(response.status_code, 201)
        self.assertEqual(set(response.json()), {"message"})
        with self.connect() as connection:
            user = connection.execute("SELECT * FROM users WHERE username = %s", (self.account["username"],)).fetchone()
        self.assertTrue(user["password_hash"].startswith("$argon2id$"))
        self.assertNotEqual(user["password_hash"], self.account["password"])
        self.assertTrue(password_hasher.verify(user["password_hash"], self.account["password"]))

    def test_duplicate_username_and_case_variant(self):
        self.assertEqual(self.register().status_code, 201)
        for name in (self.account["username"], self.account["username"].upper()):
            response = self.register(username=name, email=uuid4().hex + "@example.com")
            self.assertEqual(response.status_code, 409)

    def test_duplicate_email_and_case_variant(self):
        self.assertEqual(self.register().status_code, 201)
        for email in (self.account["email"], self.account["email"].upper()):
            response = self.register(username="other_" + uuid4().hex[:10], email=email)
            self.assertEqual(response.status_code, 409)

    def test_invalid_registration_and_no_password_echo(self):
        changes = [
            {"username": ""}, {"username": "ab"}, {"username": "name@domain"},
            {"username": "x" * 51}, {"email": "invalid"},
            {"password": "short"}, {"password": " " * 8}, {"password": "x" * 129},
            {"password": None}, {"username": 123},
            {"extra": "disallowed"},
        ]
        for change in changes:
            with self.subTest(change=list(change)):
                response = self.register(**change)
                self.assertEqual(response.status_code, 422)
                self.assertNotIn(self.account["password"], response.text)
                self.assertNotIn('"input"', response.text)
                self.assertNotIn("password_hash", response.text)

    def test_login_with_username_email_and_case(self):
        self.assertEqual(self.register().status_code, 201)
        for identifier in (self.account["username"], self.account["username"].upper(), self.account["email"].upper()):
            response = self.login(identifier=identifier)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(set(response.json()), {"id", "username", "email"})

    def test_wrong_password_and_unknown_user_have_identical_error(self):
        self.assertEqual(self.register().status_code, 201)
        wrong_password = self.login(password="wrong_password")
        unknown_user = self.login(identifier="missing_" + uuid4().hex)
        self.assertEqual(wrong_password.status_code, 401)
        self.assertEqual(unknown_user.status_code, 401)
        self.assertEqual(wrong_password.json(), unknown_user.json())
        self.assertNotIn("set-cookie", wrong_password.headers)

    def test_me_without_cookie(self):
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_me_persists_across_requests_and_does_not_expose_hash(self):
        self.create_and_login()
        for _ in range(2):
            response = self.client.get("/api/auth/me")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["username"], self.account["username"])
            self.assertEqual(response.json()["email"], self.account["email"])
            self.assertNotIn("password", response.text)
            self.assertEqual(response.headers["cache-control"], "no-store")

    def test_cookie_flags_and_jwt_claims(self):
        response = self.create_and_login()
        cookie = response.headers["set-cookie"].lower()
        for attribute in ("httponly", "samesite=lax", "path=/api", "max-age=3600"):
            self.assertIn(attribute, cookie)
        token = self.client.cookies.get(COOKIE_NAME)
        claims = jwt.decode(token, get_auth_settings().jwt_secret, algorithms=["HS256"])
        self.assertEqual(set(claims), {"sub", "iat", "exp"})
        self.assertEqual(claims["exp"] - claims["iat"], 3600)

    def test_secure_cookie_when_enabled(self):
        self.assertEqual(self.register().status_code, 201)
        with patch.dict(os.environ, {"COOKIE_SECURE": "true"}):
            get_auth_settings.cache_clear()
            try:
                self.assertIn("; Secure", self.login().headers["set-cookie"])
            finally:
                get_auth_settings.cache_clear()

    def test_logout_clears_cookie_and_protected_endpoint(self):
        self.create_and_login()
        copied_token = self.client.cookies.get(COOKIE_NAME)
        response = self.client.post("/api/auth/logout")
        self.assertEqual(response.status_code, 200)
        self.assertIn("Max-Age=0", response.headers["set-cookie"])
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)
        self.client.cookies.set(COOKIE_NAME, copied_token, path=COOKIE_PATH)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

    def test_expired_invalid_and_forged_tokens(self):
        now = datetime.now(timezone.utc)
        settings = get_auth_settings()
        tokens = [
            "not-a-jwt",
            jwt.encode({"sub": "1", "iat": now, "exp": now - timedelta(seconds=1)}, settings.jwt_secret, algorithm="HS256"),
            jwt.encode({"sub": "1", "iat": now, "exp": now + timedelta(minutes=1)}, "other-key-" * 8, algorithm="HS256"),
            jwt.encode({"sub": "1", "iat": now}, settings.jwt_secret, algorithm="HS256"),
            jwt.encode({"sub": "not-an-id", "iat": now, "exp": now + timedelta(minutes=1)}, settings.jwt_secret, algorithm="HS256"),
            jwt.encode({"sub": "9" * 100, "iat": now, "exp": now + timedelta(minutes=1)}, settings.jwt_secret, algorithm="HS256"),
        ]
        for token in tokens:
            self.client.cookies.clear()
            self.client.cookies.set(COOKIE_NAME, token, path=COOKIE_PATH)
            self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_deleted_user_cannot_use_old_token(self):
        self.create_and_login()
        with self.connect() as connection:
            connection.execute("DELETE FROM users WHERE username = %s", (self.account["username"],))
        self.assertEqual(self.client.get("/api/auth/me").status_code, 401)

    def test_csrf_blocks_wrong_missing_and_null_origin(self):
        for endpoint in ("register", "login", "logout"):
            for origin in ("https://evil.example", "null", ORIGIN + ".evil.example"):
                response = self.client.post("/api/auth/" + endpoint, json={}, headers={"Origin": origin})
                self.assertEqual(response.status_code, 403)
            request = self.client.build_request("POST", "/api/auth/" + endpoint, json={})
            del request.headers["origin"]
            self.assertEqual(self.client.send(request).status_code, 403)

    def test_sql_injection_identifier_does_not_log_in(self):
        self.assertEqual(self.login(identifier="' OR 1=1 --").status_code, 401)

    def test_simultaneous_duplicate_registration(self):
        def send_registration(_):
            with TestClient(app, headers={"Origin": ORIGIN}) as client:
                return client.post("/api/auth/register", json=self.account).status_code
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(send_registration, range(2)))
        self.assertEqual(sorted(results), [201, 409])

    def test_health_with_real_database(self):
        response = self.client.get("/api/health")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["database"], "connected")

    def test_migration_is_repeatable_and_preserves_users(self):
        self.assertEqual(self.register().status_code, 201)
        migration = (ROOT / "migrations/002_users_case_insensitive.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            for _ in range(2):
                connection.execute(migration)
            user = connection.execute("SELECT username FROM users WHERE username = %s", (self.account["username"],)).fetchone()
        self.assertEqual(user["username"], self.account["username"])

    def test_migration_preserves_legacy_data_and_rejects_case_conflicts(self):
        migration = (ROOT / "migrations/002_users_case_insensitive.sql").read_text(encoding="utf-8")
        for field in ("username", "email"):
            legacy_schema = "minix_legacy_" + uuid4().hex
            with psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=3) as admin:
                admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(legacy_schema)))
            try:
                def legacy_connect():
                    return psycopg.connect(TEST_DATABASE_URL, connect_timeout=3, options=f"-c search_path={legacy_schema}")

                with legacy_connect() as connection:
                    connection.execute("CREATE TABLE users (id SERIAL PRIMARY KEY, username TEXT UNIQUE NOT NULL, email TEXT UNIQUE NOT NULL)")
                    rows = [("CaseName", "first@example.com"), ("casename", "second@example.com")] if field == "username" else [("first", "Case@example.com"), ("second", "case@example.com")]
                    connection.cursor().executemany("INSERT INTO users (username, email) VALUES (%s, %s)", rows)
                with self.assertRaises(psycopg.errors.RaiseException):
                    with legacy_connect() as connection:
                        connection.execute(migration)
                with legacy_connect() as connection:
                    self.assertEqual(connection.execute("SELECT username, email FROM users ORDER BY id").fetchall(), rows)
                    connection.execute("UPDATE users SET username = %s, email = %s WHERE id = 2", ("resolved", "resolved@example.com"))
                with legacy_connect() as connection:
                    connection.execute(migration)
                    self.assertEqual(connection.execute("SELECT COUNT(*) FROM users").fetchone()[0], 2)
                    self.assertEqual(connection.execute("SELECT username, email FROM users WHERE id = 1").fetchone(), rows[0])
            finally:
                with psycopg.connect(TEST_DATABASE_URL, autocommit=True, connect_timeout=3) as admin:
                    admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(legacy_schema)))

    def test_database_failure_returns_safe_json(self):
        with patch("backend.auth.get_connection", side_effect=psycopg.OperationalError("private-connection-password")):
            response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertIn("message", response.json())
        self.assertNotIn("private-connection-password", response.text)

    def test_missing_jwt_secret_prevents_startup(self):
        with patch.dict(os.environ, {"JWT_SECRET": ""}):
            get_auth_settings.cache_clear()
            try:
                with self.assertRaises(RuntimeError):
                    with TestClient(app):
                        pass
            finally:
                get_auth_settings.cache_clear()

if __name__ == "__main__":
    unittest.main()
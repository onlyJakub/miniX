import json
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
import jwt
import psycopg
from backend.auth import COOKIE_NAME, COOKIE_PATH
from backend.config import get_auth_settings
from backend.test_support import ORIGIN, DatabaseTestCase

class PostsTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.execute("DELETE FROM posts")

    def publish(self, content="Pierwszy post"):
        return self.client.post("/api/posts", json={"content": content})

    def own_post(self, content="Pierwszy post"):
        user = self.create_and_login().json()
        response = self.publish(content)
        self.assertEqual(response.status_code, 201)
        return user, response.json()

    def test_create_persists_content_and_session_author(self):
        user, post = self.own_post()
        self.assertEqual(
            set(post),
            {
                "id",
                "content",
                "created_at",
                "is_anonymous",
                "author",
                "is_owner",
                "likes_count",
                "liked_by_me",
            },
        )
        self.assertFalse(post["is_anonymous"])
        self.assertTrue(post["is_owner"])
        self.assertEqual(
            post["author"], {"id": user["id"], "username": user["username"]}
        )
        self.assertEqual(post["likes_count"], 0)
        self.assertFalse(post["liked_by_me"])
        self.assertTrue(post["created_at"].endswith("Z"))
        self.assertIsNotNone(datetime.fromisoformat(post["created_at"]).tzinfo)
        with self.connect() as connection:
            stored = connection.execute(
                "SELECT user_id, content FROM posts WHERE id = %s", (post["id"],)
            ).fetchone()
        self.assertEqual(stored, {"user_id": user["id"], "content": "Pierwszy post"})

    def test_guest_cannot_publish(self):
        self.assertEqual(self.publish().status_code, 401)

    def test_invalid_empty_blank_and_nontext_content(self):
        self.create_and_login()
        for content in ("", " ", "\n\t\r ", "\u00a0\u2003", None, 123, [], "a\x00b"):
            with self.subTest(content=repr(content)):
                response = self.publish(content)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["errors"][0]["field"], "content")
                self.assertNotIn('"input"', response.text)
        malformed_unicode = self.client.post(
            "/api/posts",
            content=json.dumps({"content": "\ud800"}, ensure_ascii=True),
            headers={"Content-Type": "application/json"},
        )
        self.assertEqual(malformed_unicode.status_code, 422)
        self.assertEqual(self.client.post("/api/posts", json={}).status_code, 422)
        self.assertEqual(self.client.get("/api/posts").json(), [])

    def test_long_content_is_rejected(self):
        self.create_and_login()
        for content in ("a" * 281, "😀" * 281, "ą" * 278 + "👩‍🔬"):
            self.assertEqual(self.publish(content).status_code, 422)

    def test_279_and_280_unicode_code_points_are_accepted(self):
        self.create_and_login()
        for content in ("ą" * 278 + "😀", "ą" * 277 + "👩‍🔬", "😀" * 280):
            response = self.publish(content)
            self.assertEqual(response.status_code, 201)
            self.assertEqual(response.json()["content"], content)

    def test_trimming_before_length_check_preserves_inner_newlines(self):
        self.create_and_login()
        response = self.publish(" \n\t Pierwsza linia\nDruga linia  \r\n")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["content"], "Pierwsza linia\nDruga linia")
        self.assertEqual(self.publish("  " + "a" * 280 + "\n").status_code, 201)
        self.assertEqual(self.publish("\u0085" + "ą" * 279 + "😀\u2003").status_code, 201)

    def test_client_cannot_choose_author_or_date(self):
        self.create_and_login()
        for field, value in (
            ("user_id", 999),
            ("author", {"id": 999}),
            ("created_at", "2020-01-01T00:00:00Z"),
        ):
            response = self.client.post(
                "/api/posts", json={"content": "Próba", field: value}
            )
            self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get("/api/posts").json(), [])

    def test_empty_authenticated_feed(self):
        self.create_and_login()
        response = self.client.get("/api/posts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [])

    def test_authenticated_feed_and_author_join_do_not_expose_private_data(self):
        user, post = self.own_post()
        response = self.client.get("/api/posts")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), [post])
        self.assertNotIn(user["email"], response.text)
        self.assertNotIn("password", response.text)
        self.assertEqual(set(response.json()[0]["author"]), {"id", "username"})

    def test_sort_by_date_then_id(self):
        user = self.create_and_login().json()
        now = datetime.now(timezone.utc)
        ids = []
        with self.connect() as connection:
            for date in (now, now - timedelta(days=1), now, now - timedelta(days=2)):
                ids.append(
                    connection.execute(
                        "INSERT INTO posts (user_id, content, created_at) VALUES (%s, %s, %s) RETURNING id",
                        (user["id"], "Wpis", date),
                    ).fetchone()["id"]
                )
        result = self.client.get("/api/posts").json()
        self.assertEqual(
            [post["id"] for post in result], [ids[2], ids[0], ids[1], ids[3]]
        )

    def test_default_and_custom_pagination(self):
        user = self.create_and_login().json()
        with self.connect() as connection:
            connection.cursor().executemany(
                "INSERT INTO posts (user_id, content) VALUES (%s, %s)",
                [(user["id"], f"Wpis {index}") for index in range(55)],
            )
        default = self.client.get("/api/posts").json()
        first = self.client.get("/api/posts?limit=50&offset=0").json()
        second = self.client.get("/api/posts?limit=10&offset=50").json()
        middle = self.client.get("/api/posts?limit=7&offset=7").json()
        self.assertEqual(len(default), 20)
        self.assertEqual(default, first[:20])
        self.assertEqual(len(first), 50)
        self.assertEqual(len(second), 5)
        self.assertTrue(
            set(post["id"] for post in first).isdisjoint(post["id"] for post in second)
        )
        self.assertEqual(middle, first[7:14])
        self.assertEqual(self.client.get("/api/posts?offset=100").json(), [])

    def test_invalid_pagination(self):
        self.create_and_login()
        for query in (
            "limit=0",
            "limit=51",
            "limit=-1",
            "limit=abc",
            "offset=-1",
            "offset=1.5",
            "offset=" + "9" * 30,
        ):
            response = self.client.get("/api/posts?" + query)
            self.assertEqual(response.status_code, 422)
            self.assertIn(response.json()["errors"][0]["field"], ("limit", "offset"))

    def test_author_deletes_own_post_with_empty_204(self):
        _, post = self.own_post()
        response = self.client.delete(f"/api/posts/{post['id']}")
        self.assertEqual(response.status_code, 204)
        self.assertEqual(response.content, b"")
        self.assertEqual(self.client.get("/api/posts").json(), [])
        with self.connect() as connection:
            self.assertIsNone(
                connection.execute(
                    "SELECT id FROM posts WHERE id = %s", (post["id"],)
                ).fetchone()
            )

    def test_other_user_cannot_delete_post(self):
        _, post = self.own_post()
        other = {
            "username": "other_" + uuid4().hex[:12],
            "email": uuid4().hex + "@example.com",
            "password": "Other_password_123!",
        }
        self.assertEqual(
            self.client.post("/api/auth/register", json=other).status_code, 201
        )
        self.assertEqual(
            self.client.post(
                "/api/auth/login",
                json={"identifier": other["username"], "password": other["password"]},
            ).status_code,
            200,
        )
        self.assertEqual(
            self.client.delete(f"/api/posts/{post['id']}").status_code, 403
        )
        self.assertEqual(len(self.client.get("/api/posts").json()), 1)

    def test_guest_cannot_delete_post(self):
        _, post = self.own_post()
        self.client.cookies.clear()
        self.assertEqual(
            self.client.delete(f"/api/posts/{post['id']}").status_code, 401
        )
        self.assertEqual(self.client.get("/api/posts").status_code, 401)
        with self.connect() as connection:
            self.assertIsNotNone(
                connection.execute(
                    "SELECT id FROM posts WHERE id = %s", (post["id"],)
                ).fetchone()
            )

    def test_missing_post_returns_404_for_authenticated_user(self):
        self.create_and_login()
        self.assertEqual(self.client.delete("/api/posts/2147483647").status_code, 404)

    def test_invalid_post_id(self):
        self.create_and_login()
        for post_id in ("0", "-1", "abc", "2147483648"):
            self.assertEqual(
                self.client.delete("/api/posts/" + post_id).status_code, 422
            )

    def test_special_characters_multiline_html_and_sql_are_plain_text(self):
        text = 'Zażółć gęślą jaźń 😀\n<script>alert("xss")</script>\n<img src=x onerror=alert(1)>\n\' ; DROP TABLE users; -- & < >'
        _, post = self.own_post(text)
        self.assertEqual(post["content"], text)
        self.assertEqual(self.client.get("/api/posts").json()[0]["content"], text)
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)

    def test_csrf_protects_create_and_delete(self):
        _, post = self.own_post()
        for method, path, body in (
            ("POST", "/api/posts", {"content": "Próba CSRF"}),
            ("DELETE", f"/api/posts/{post['id']}", None),
        ):
            for origin in ("https://evil.example", "null", ORIGIN + ".evil.example"):
                self.assertEqual(
                    self.client.request(
                        method, path, json=body, headers={"Origin": origin}
                    ).status_code,
                    403,
                )
            request = self.client.build_request(method, path, json=body)
            del request.headers["origin"]
            self.assertEqual(self.client.send(request).status_code, 403)
        self.assertEqual(len(self.client.get("/api/posts").json()), 1)

    def test_expired_session_cannot_read_or_mutate(self):
        user, post = self.own_post()
        now = datetime.now(timezone.utc)
        token = jwt.encode(
            {
                "sub": str(user["id"]),
                "iat": now - timedelta(hours=2),
                "exp": now - timedelta(hours=1),
            },
            get_auth_settings().jwt_secret,
            algorithm="HS256",
        )
        self.client.cookies.clear()
        self.client.cookies.set(COOKIE_NAME, token, path=COOKIE_PATH)
        self.assertEqual(self.publish().status_code, 401)
        self.assertEqual(
            self.client.delete(f"/api/posts/{post['id']}").status_code, 401
        )
        self.assertEqual(self.client.get("/api/posts").status_code, 401)

    def test_guest_cannot_read_feed(self):
        response = self.client.get("/api/posts")
        self.assertEqual(response.status_code, 401)
        self.assertIn("message", response.json())

    def test_invalid_or_forged_token_cannot_read_feed(self):
        now = datetime.now(timezone.utc)
        forged = jwt.encode(
            {"sub": "1", "iat": now, "exp": now + timedelta(hours=1)},
            "wrong-signing-key-" * 4,
            algorithm="HS256",
        )
        for token in ("invalid.jwt.token", forged):
            self.client.cookies.clear()
            self.client.cookies.set(COOKIE_NAME, token, path=COOKIE_PATH)
            self.assertEqual(self.client.get("/api/posts").status_code, 401)

    def test_database_enforces_content_and_foreign_key_constraints(self):
        user = self.create_and_login().json()
        for content, error_type in (
            ("", psycopg.errors.CheckViolation),
            (" \t\n", psycopg.errors.CheckViolation),
            ("x" * 281, psycopg.errors.StringDataRightTruncation),
        ):
            with self.assertRaises(error_type):
                with self.connect() as connection:
                    connection.execute(
                        "INSERT INTO posts (user_id, content) VALUES (%s, %s)",
                        (user["id"], content),
                    )
        with self.assertRaises(psycopg.errors.ForeignKeyViolation):
            with self.connect() as connection:
                connection.execute(
                    "INSERT INTO posts (user_id, content) VALUES (%s, %s)",
                    (2147483647, "Brak autora"),
                )

    def test_existing_likes_foreign_key_cascades_when_post_deleted(self):
        user, post = self.own_post()
        with self.connect() as connection:
            connection.execute(
                "INSERT INTO likes (user_id, post_id) VALUES (%s, %s)",
                (user["id"], post["id"]),
            )
        self.assertEqual(
            self.client.delete(f"/api/posts/{post['id']}").status_code, 204
        )
        with self.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS count FROM likes WHERE post_id = %s",
                    (post["id"],),
                ).fetchone()["count"],
                0,
            )

    def test_database_errors_do_not_leak_connection_details(self):
        self.create_and_login()
        with patch(
            "backend.posts.get_connection",
            side_effect=psycopg.OperationalError("private-database-password"),
        ):
            response = self.client.get("/api/posts")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("private-database-password", response.text)

if __name__ == "__main__":
    unittest.main()
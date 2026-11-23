import unittest
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from uuid import uuid4
import jwt
import psycopg
from fastapi.testclient import TestClient
from backend.auth import COOKIE_NAME, COOKIE_PATH
from backend.config import get_auth_settings
from backend.main import app
from backend.test_support import ORIGIN, DatabaseTestCase

class LikesTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.execute("DELETE FROM posts")
        self.user = self.create_and_login().json()
        response = self.client.post(
            "/api/posts", json={"content": "Post z polubieniami"}
        )
        self.assertEqual(response.status_code, 201)
        self.post = response.json()
        self.path = f"/api/posts/{self.post['id']}/like"

    def login_other_user(self):
        suffix = uuid4().hex[:12]
        account = {
            "username": "other_" + suffix,
            "email": suffix + "@example.com",
            "password": "Other_password_123!",
        }
        self.assertEqual(
            self.client.post("/api/auth/register", json=account).status_code, 201
        )
        response = self.client.post(
            "/api/auth/login",
            json={"identifier": account["username"], "password": account["password"]},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def feed_post(self):
        response = self.client.get("/api/posts")
        self.assertEqual(response.status_code, 200)
        return next(post for post in response.json() if post["id"] == self.post["id"])

    def test_author_can_like_own_post_and_created_at_is_preserved(self):
        response = self.client.post(self.path)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"likes_count": 1, "liked_by_me": True})
        with self.connect() as connection:
            like = connection.execute(
                "SELECT user_id, post_id, created_at FROM likes"
            ).fetchone()
        self.assertEqual(like["user_id"], self.user["id"])
        self.assertEqual(like["post_id"], self.post["id"])
        self.assertIsNotNone(like["created_at"].tzinfo)
        self.assertEqual(self.feed_post()["created_at"], self.post["created_at"])

    def test_guest_cannot_like_or_unlike(self):
        self.client.cookies.clear()
        for method in ("POST", "DELETE"):
            self.assertEqual(self.client.request(method, self.path).status_code, 401)

    def test_repeated_like_is_idempotent(self):
        for _ in range(3):
            response = self.client.post(self.path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"likes_count": 1, "liked_by_me": True})
        with self.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS n FROM likes").fetchone()["n"], 1
            )

    def test_multiple_users_can_like_and_only_remove_their_own_like(self):
        self.assertEqual(self.client.post(self.path).json()["likes_count"], 1)
        other = self.login_other_user()
        self.assertEqual(
            self.client.post(self.path).json(), {"likes_count": 2, "liked_by_me": True}
        )
        self.assertEqual(
            self.client.delete(self.path).json(),
            {"likes_count": 1, "liked_by_me": False},
        )
        self.assertEqual(
            self.client.delete(self.path).json(),
            {"likes_count": 1, "liked_by_me": False},
        )
        with self.connect() as connection:
            users = connection.execute(
                "SELECT user_id FROM likes WHERE post_id = %s", (self.post["id"],)
            ).fetchall()
        self.assertEqual(users, [{"user_id": self.user["id"]}])
        self.assertNotEqual(other["id"], self.user["id"])

    def test_unlike_and_repeated_unlike_are_idempotent(self):
        self.client.post(self.path)
        for _ in range(3):
            response = self.client.delete(self.path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"likes_count": 0, "liked_by_me": False})
        with self.connect() as connection:
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS n FROM likes").fetchone()["n"], 0
            )

    def test_unlike_without_existing_like_does_not_touch_other_users(self):
        self.client.post(self.path)
        self.login_other_user()
        self.assertEqual(
            self.client.delete(self.path).json(),
            {"likes_count": 1, "liked_by_me": False},
        )
        self.assertEqual(self.feed_post()["likes_count"], 1)

    def test_missing_post_returns_404_for_both_operations(self):
        for method in ("POST", "DELETE"):
            self.assertEqual(
                self.client.request(method, "/api/posts/2147483647/like").status_code,
                404,
            )

    def test_invalid_post_id_returns_422(self):
        for method in ("POST", "DELETE"):
            for post_id in ("0", "-1", "abc", "2147483648"):
                self.assertEqual(
                    self.client.request(
                        method, f"/api/posts/{post_id}/like"
                    ).status_code,
                    422,
                )

    def test_feed_reports_counts_and_viewer_state_including_zero(self):
        self.assertEqual(self.post["likes_count"], 0)
        self.assertFalse(self.post["liked_by_me"])
        self.assertEqual(self.feed_post(), self.post)
        self.client.post(self.path)
        self.assertEqual(self.feed_post()["likes_count"], 1)
        self.assertTrue(self.feed_post()["liked_by_me"])
        self.login_other_user()
        self.assertEqual(self.feed_post()["likes_count"], 1)
        self.assertFalse(self.feed_post()["liked_by_me"])
        self.client.post(self.path)
        self.assertEqual(self.feed_post()["likes_count"], 2)
        self.assertTrue(self.feed_post()["liked_by_me"])

    def test_likes_survive_feed_reload_and_login(self):
        self.client.post(self.path)
        for _ in range(2):
            self.assertEqual(self.feed_post()["likes_count"], 1)
            self.assertTrue(self.feed_post()["liked_by_me"])
        self.assertEqual(self.client.post("/api/auth/logout").status_code, 200)
        self.assertEqual(self.login().status_code, 200)
        self.assertTrue(self.feed_post()["liked_by_me"])

    def test_deleting_post_cascades_likes(self):
        self.client.post(self.path)
        self.assertEqual(
            self.client.delete(f"/api/posts/{self.post['id']}").status_code, 204
        )
        with self.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM likes WHERE post_id = %s",
                    (self.post["id"],),
                ).fetchone()["n"],
                0,
            )
        self.assertEqual(self.client.post(self.path).status_code, 404)
        self.assertEqual(self.client.delete(self.path).status_code, 404)

    def test_origin_protects_both_operations(self):
        for method in ("POST", "DELETE"):
            for origin in ("https://evil.example", "null", ORIGIN + ".evil.example"):
                self.assertEqual(
                    self.client.request(
                        method, self.path, headers={"Origin": origin}
                    ).status_code,
                    403,
                )
            request = self.client.build_request(method, self.path)
            del request.headers["origin"]
            self.assertEqual(self.client.send(request).status_code, 403)
        self.assertFalse(self.feed_post()["liked_by_me"])

    def test_expired_and_forged_sessions_are_rejected(self):
        now = datetime.now(timezone.utc)
        tokens = (
            jwt.encode(
                {
                    "sub": str(self.user["id"]),
                    "iat": now - timedelta(hours=2),
                    "exp": now - timedelta(hours=1),
                },
                get_auth_settings().jwt_secret,
                algorithm="HS256",
            ),
            jwt.encode(
                {
                    "sub": str(self.user["id"]),
                    "iat": now,
                    "exp": now + timedelta(hours=1),
                },
                "wrong-signing-key-" * 4,
                algorithm="HS256",
            ),
            "invalid.jwt.token",
        )
        for token in tokens:
            self.client.cookies.clear()
            self.client.cookies.set(COOKIE_NAME, token, path=COOKIE_PATH)
            for method in ("POST", "DELETE"):
                self.assertEqual(
                    self.client.request(method, self.path).status_code, 401
                )

    def test_database_enforces_unique_pair_and_foreign_keys(self):
        self.client.post(self.path)
        with self.assertRaises(psycopg.errors.UniqueViolation):
            with self.connect() as connection:
                connection.execute(
                    "INSERT INTO likes (user_id, post_id) VALUES (%s, %s)",
                    (self.user["id"], self.post["id"]),
                )
        for user_id, post_id in (
            (2147483647, self.post["id"]),
            (self.user["id"], 2147483647),
        ):
            with self.assertRaises(psycopg.errors.ForeignKeyViolation):
                with self.connect() as connection:
                    connection.execute(
                        "INSERT INTO likes (user_id, post_id) VALUES (%s, %s)",
                        (user_id, post_id),
                    )

    def test_parallel_retries_do_not_duplicate_likes(self):
        cookies = dict(self.client.cookies)

        def send(method):
            with TestClient(app, headers={"Origin": ORIGIN}, cookies=cookies) as client:
                response = client.request(method, self.path)
                return response.status_code, response.json()

        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(send, ["POST"] * 4))
        self.assertEqual(results, [(200, {"likes_count": 1, "liked_by_me": True})] * 4)
        with ThreadPoolExecutor(max_workers=4) as executor:
            results = list(executor.map(send, ["DELETE"] * 4))
        self.assertEqual(results, [(200, {"likes_count": 0, "liked_by_me": False})] * 4)

    def test_likes_do_not_change_feed_sort_or_pagination(self):
        newer = self.client.post("/api/posts", json={"content": "Nowszy post"}).json()
        self.client.post(self.path)
        self.assertEqual(
            self.client.get("/api/posts?limit=1").json()[0]["id"], newer["id"]
        )
        older = self.client.get("/api/posts?limit=1&offset=1").json()[0]
        self.assertEqual(older["id"], self.post["id"])
        self.assertEqual(older["likes_count"], 1)
        self.assertTrue(older["liked_by_me"])

if __name__ == "__main__":
    unittest.main()
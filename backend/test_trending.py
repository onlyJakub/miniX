import unittest
from contextlib import nullcontext
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
from uuid import uuid4
import jwt
from backend.auth import COOKIE_NAME, COOKIE_PATH
from backend.config import get_auth_settings
from backend.test_support import DatabaseTestCase

class TrendingTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.execute("DELETE FROM posts")
        self.user = self.create_and_login().json()
        self.voters = []
        with self.connect() as connection:
            for _ in range(4):
                suffix = uuid4().hex
                voter = connection.execute(
                    "INSERT INTO users (username, email, password_hash) VALUES (%s, %s, %s) RETURNING id",
                    ("voter_" + suffix, suffix + "@example.com", "unused-test-hash"),
                ).fetchone()
                self.voters.append(voter["id"])

    def make_post(self, hours_ago=1, likes=0, content="Wpis do rankingu"):
        with self.connect() as connection:
            post_id = connection.execute(
                "INSERT INTO posts (user_id, content, created_at) "
                "VALUES (%s, %s, NOW() - %s * INTERVAL '1 hour') RETURNING id",
                (self.user["id"], content, hours_ago),
            ).fetchone()["id"]
            for user_id in self.voters[:likes]:
                connection.execute("INSERT INTO likes (user_id, post_id) VALUES (%s, %s)", (user_id, post_id))
        return post_id

    def ranking(self):
        response = self.client.get("/api/posts/trending")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["cache-control"], "no-store")
        return response.json()

    def test_guest_cannot_read_ranking(self):
        self.client.cookies.clear()
        self.assertEqual(self.client.get("/api/posts/trending").status_code, 401)

    def test_expired_invalid_and_forged_sessions_are_rejected(self):
        now = datetime.now(timezone.utc)
        claims = {"sub": str(self.user["id"]), "iat": now - timedelta(hours=2), "exp": now - timedelta(hours=1)}
        expired = jwt.encode(claims, get_auth_settings().jwt_secret, algorithm="HS256")
        claims["exp"] = now + timedelta(hours=1)
        forged = jwt.encode(claims, "wrong-signing-key-" * 4, algorithm="HS256")
        for token in (expired, forged, "invalid.jwt.token"):
            self.client.cookies.clear()
            self.client.cookies.set(COOKIE_NAME, token, path=COOKIE_PATH)
            self.assertEqual(self.client.get("/api/posts/trending").status_code, 401)

    def test_empty_database_returns_empty_list(self):
        self.assertEqual(self.ranking(), [])

    def test_one_and_two_posts_with_zero_likes_are_included(self):
        older = self.make_post(hours_ago=2)
        self.assertEqual([post["id"] for post in self.ranking()], [older])
        newer = self.make_post(hours_ago=1)
        posts = self.ranking()
        self.assertEqual([post["id"] for post in posts], [newer, older])
        self.assertEqual([post["likes_count"] for post in posts], [0, 0])
        self.assertTrue(all(post["liked_by_me"] is False for post in posts))

    def test_ranking_sorts_by_likes_and_returns_only_top_three(self):
        ids = [self.make_post(hours_ago=age, likes=count) for age, count in ((1, 1), (3, 4), (2, 2), (4, 3))]
        posts = self.ranking()
        self.assertEqual([post["id"] for post in posts], [ids[1], ids[3], ids[2]])
        self.assertEqual([post["likes_count"] for post in posts], [4, 3, 2])

    def test_old_post_is_excluded_even_with_more_likes(self):
        self.make_post(hours_ago=25, likes=4)
        recent = self.make_post(hours_ago=23.5, likes=0)
        self.assertEqual([post["id"] for post in self.ranking()], [recent])

    def test_only_old_posts_return_empty_list(self):
        self.make_post(hours_ago=48, likes=4)
        self.assertEqual(self.ranking(), [])

    def test_ties_are_resolved_by_date_then_id_descending(self):
        with self.connect() as connection:
            ids = []
            for hours_ago in (2, 1, 1):
                ids.append(connection.execute(
                    "INSERT INTO posts (user_id, content, created_at) "
                    "VALUES (%s, 'Remis', NOW() - %s * INTERVAL '1 hour') RETURNING id",
                    (self.user["id"], hours_ago),
                ).fetchone()["id"])
        self.assertEqual([post["id"] for post in self.ranking()], [ids[2], ids[1], ids[0]])

    def test_time_window_has_stable_inclusive_boundaries(self):
        with self.connect() as connection:
            ids = []
            for age in ("24 hours 0.000001 seconds", "24 hours", "0 seconds", "-0.000001 seconds"):
                ids.append(connection.execute(
                    "INSERT INTO posts (user_id, content, created_at) VALUES (%s, 'Granica', NOW() - %s::interval) RETURNING id",
                    (self.user["id"], age),
                ).fetchone()["id"])
            with patch("backend.posts.get_connection", side_effect=lambda: nullcontext(connection)):
                self.assertEqual([post["id"] for post in self.ranking()], [ids[2], ids[1]])

    def test_response_preserves_full_content_and_exposes_only_public_author_fields(self):
        text = 'Zażółć gęślą jaźń 😀 ' * 5 + '<script>alert("xss")</script>'
        post_id = self.make_post(likes=2, content=text)
        self.assertEqual(self.client.post(f"/api/posts/{post_id}/like").status_code, 200)
        post = self.ranking()[0]
        self.assertEqual(set(post), {"id", "content", "created_at", "is_anonymous", "author", "is_owner", "likes_count", "liked_by_me"})
        self.assertEqual(post["content"], text)
        self.assertEqual(post["author"], {"id": self.user["id"], "username": self.user["username"]})
        self.assertEqual(post["likes_count"], 3)
        self.assertTrue(post["liked_by_me"])
        self.assertTrue(post["created_at"].endswith("Z"))
        self.assertNotIn(self.user["email"], str(post))
        self.assertNotIn("password", str(post))

    def test_liked_by_me_is_specific_to_the_current_session(self):
        post_id = self.make_post()
        self.client.post(f"/api/posts/{post_id}/like")
        self.assertTrue(self.ranking()[0]["liked_by_me"])
        account = {"username": "other_" + uuid4().hex, "email": uuid4().hex + "@example.com", "password": "Other_password_123!"}
        self.assertEqual(self.client.post("/api/auth/register", json=account).status_code, 201)
        self.assertEqual(self.client.post("/api/auth/login", json={"identifier": account["username"], "password": account["password"]}).status_code, 200)
        post = self.ranking()[0]
        self.assertEqual(post["likes_count"], 1)
        self.assertFalse(post["liked_by_me"])

    def test_like_and_unlike_recalculate_ranking(self):
        older = self.make_post(hours_ago=2, likes=1)
        newer = self.make_post(hours_ago=1, likes=1)
        self.assertEqual([post["id"] for post in self.ranking()], [newer, older])
        self.assertEqual(self.client.post(f"/api/posts/{older}/like").status_code, 200)
        self.assertEqual([post["id"] for post in self.ranking()], [older, newer])
        self.assertEqual(self.client.delete(f"/api/posts/{older}/like").status_code, 200)
        self.assertEqual([post["id"] for post in self.ranking()], [newer, older])

    def test_deleted_post_is_removed_from_ranking(self):
        post_id = self.make_post(likes=4)
        self.assertEqual(self.client.delete(f"/api/posts/{post_id}").status_code, 204)
        self.assertEqual(self.ranking(), [])

if __name__ == "__main__":
    unittest.main()
import unittest
from uuid import uuid4
from backend.test_support import ORIGIN, ROOT, DatabaseTestCase

class AnonymousPostsTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.execute("DELETE FROM posts")
        self.owner = self.create_and_login().json()

    def publish(self, **changes):
        return self.client.post(
            "/api/posts",
            json={"content": "Prywatna opinia", "is_anonymous": True} | changes,
        )

    def own_anonymous_post(self):
        response = self.publish()
        self.assertEqual(response.status_code, 201)
        return response.json()

    def login_other_user(self):
        suffix = uuid4().hex[:12]
        account = {
            "username": "viewer_" + suffix,
            "email": suffix + "@example.com",
            "password": "Viewer_password_123!",
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

    def assert_anonymous(self, post, is_owner):
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
        self.assertTrue(post["is_anonymous"])
        self.assertIsNone(post["author"])
        self.assertEqual(post["is_owner"], is_owner)
        self.assertNotIn(self.owner["username"], str(post))
        self.assertNotIn(self.owner["email"], str(post))
        for field in (
            "user_id",
            "author_id",
            "username",
            "email",
            "password_hash",
            "avatar",
            "profile",
        ):
            self.assertNotIn(field, post)

    def test_create_hides_author_and_persists_real_owner(self):
        post = self.own_anonymous_post()
        self.assert_anonymous(post, True)
        self.assertEqual(post["likes_count"], 0)
        self.assertFalse(post["liked_by_me"])
        with self.connect() as connection:
            stored = connection.execute(
                "SELECT user_id, is_anonymous, content FROM posts WHERE id = %s",
                (post["id"],),
            ).fetchone()
        self.assertEqual(
            stored,
            {
                "user_id": self.owner["id"],
                "is_anonymous": True,
                "content": post["content"],
            },
        )

    def test_omitted_flag_and_explicit_false_keep_public_author(self):
        for body in (
            {"content": "Zwykły wpis"},
            {"content": "Jawny wpis", "is_anonymous": False},
        ):
            response = self.client.post("/api/posts", json=body)
            self.assertEqual(response.status_code, 201)
            post = response.json()
            self.assertFalse(post["is_anonymous"])
            self.assertTrue(post["is_owner"])
            self.assertEqual(
                post["author"],
                {"id": self.owner["id"], "username": self.owner["username"]},
            )
        self.login_other_user()
        self.assertTrue(
            all(
                post["is_owner"] is False
                for post in self.client.get("/api/posts").json()
            )
        )

    def test_owner_and_other_viewer_both_receive_hidden_author_in_all_feeds(self):
        created = self.own_anonymous_post()
        for owner_view in (True, False):
            if not owner_view:
                self.login_other_user()
            for path in ("/api/posts", "/api/posts/trending"):
                response = self.client.get(path)
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.headers["cache-control"], "no-store")
                self.assertEqual(len(response.json()), 1)
                self.assert_anonymous(response.json()[0], owner_view)
                self.assertEqual(response.json()[0]["id"], created["id"])

    def test_guest_cannot_publish_anonymous_post(self):
        self.client.cookies.clear()
        self.assertEqual(self.publish().status_code, 401)

    def test_flag_requires_a_real_json_boolean(self):
        for value in (None, "true", "false", "yes", 0, 1, [], {}):
            with self.subTest(value=value):
                response = self.publish(is_anonymous=value)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["errors"][0]["field"], "is_anonymous")
        self.assertEqual(self.client.get("/api/posts").json(), [])

    def test_client_cannot_supply_author_or_ownership(self):
        for field, value in (
            ("user_id", 999),
            ("author", {"id": 999}),
            ("is_owner", True),
        ):
            self.assertEqual(self.publish(**{field: value}).status_code, 422)
        self.assertEqual(self.client.get("/api/posts").json(), [])

    def test_content_validation_is_preserved_for_anonymous_posts(self):
        for content in ("", " \n\t", "x" * 281, "😀" * 281, "\x00"):
            self.assertEqual(self.publish(content=content).status_code, 422)
        response = self.publish(content="  " + "😀" * 280 + " \n")
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["content"], "😀" * 280)
        self.assert_anonymous(response.json(), True)

    def test_origin_protects_anonymous_publication(self):
        for origin in ("https://evil.example", "null", ORIGIN + ".evil.example"):
            self.assertEqual(
                self.client.post(
                    "/api/posts",
                    json={"content": "Próba", "is_anonymous": True},
                    headers={"Origin": origin},
                ).status_code,
                403,
            )
        request = self.client.build_request(
            "POST", "/api/posts", json={"content": "Próba", "is_anonymous": True}
        )
        del request.headers["origin"]
        self.assertEqual(self.client.send(request).status_code, 403)
        self.assertEqual(self.client.get("/api/posts").json(), [])

    def test_other_user_cannot_delete_anonymous_post(self):
        post = self.own_anonymous_post()
        self.login_other_user()
        self.assertEqual(
            self.client.delete(f"/api/posts/{post['id']}").status_code, 403
        )
        self.assert_anonymous(self.client.get("/api/posts").json()[0], False)

    def test_owner_can_delete_and_cascade_likes_from_both_feeds(self):
        post = self.own_anonymous_post()
        self.assertEqual(
            self.client.post(f"/api/posts/{post['id']}/like").status_code, 200
        )
        self.assertEqual(
            self.client.delete(f"/api/posts/{post['id']}").status_code, 204
        )
        for path in ("/api/posts", "/api/posts/trending"):
            self.assertEqual(self.client.get(path).json(), [])
        with self.connect() as connection:
            self.assertEqual(
                connection.execute(
                    "SELECT COUNT(*) AS n FROM likes WHERE post_id = %s", (post["id"],)
                ).fetchone()["n"],
                0,
            )

    def test_likes_unlikes_and_trending_work_for_anonymous_posts(self):
        anonymous = self.own_anonymous_post()
        normal = self.publish(content="Jawny wpis", is_anonymous=False).json()
        self.login_other_user()
        path = f"/api/posts/{anonymous['id']}/like"
        for _ in range(2):
            response = self.client.post(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), {"likes_count": 1, "liked_by_me": True})
        ranking = self.client.get("/api/posts/trending").json()
        self.assertEqual(
            [post["id"] for post in ranking], [anonymous["id"], normal["id"]]
        )
        self.assert_anonymous(ranking[0], False)
        self.assertTrue(ranking[0]["liked_by_me"])
        self.assertEqual(ranking[0]["likes_count"], 1)
        self.assertEqual(
            self.client.delete(path).json(), {"likes_count": 0, "liked_by_me": False}
        )
        self.assertEqual(
            [post["id"] for post in self.client.get("/api/posts/trending").json()],
            [normal["id"], anonymous["id"]],
        )

    def test_mixed_feed_keeps_pagination_and_ownership(self):
        anonymous = self.own_anonymous_post()
        normal = self.publish(content="Jawny wpis", is_anonymous=False).json()
        first = self.client.get("/api/posts?limit=1").json()
        second = self.client.get("/api/posts?limit=1&offset=1").json()
        self.assertEqual(first, [normal])
        self.assertEqual(second, [anonymous])

    def test_migration_preserves_legacy_posts_users_and_likes(self):
        post = self.publish(content="Post sprzed migracji", is_anonymous=False).json()
        self.client.post(f"/api/posts/{post['id']}/like")
        migration = (ROOT / "migrations/003_posts_anonymous.sql").read_text(
            encoding="utf-8"
        )
        with self.connect() as connection:
            before = connection.execute(
                "SELECT id, user_id, content, created_at FROM posts"
            ).fetchall()
            connection.execute("ALTER TABLE posts DROP COLUMN is_anonymous")
            connection.execute(migration)
            after = connection.execute(
                "SELECT id, user_id, content, created_at FROM posts"
            ).fetchall()
            self.assertEqual(after, before)
            self.assertFalse(
                connection.execute("SELECT is_anonymous FROM posts").fetchone()[
                    "is_anonymous"
                ]
            )
            self.assertEqual(
                connection.execute("SELECT COUNT(*) AS n FROM likes").fetchone()["n"], 1
            )
        self.assertEqual(self.client.get("/api/auth/me").status_code, 200)
        self.assertEqual(
            self.client.get("/api/posts").json(),
            [post | {"likes_count": 1, "liked_by_me": True}],
        )

    def test_migration_is_repeatable_and_keeps_existing_anonymous_flags(self):
        self.own_anonymous_post()
        migration = (ROOT / "migrations/003_posts_anonymous.sql").read_text(
            encoding="utf-8"
        )
        with self.connect() as connection:
            for _ in range(2):
                connection.execute(migration)
            self.assertTrue(
                connection.execute("SELECT is_anonymous FROM posts").fetchone()[
                    "is_anonymous"
                ]
            )
        self.assert_anonymous(self.client.get("/api/posts").json()[0], True)

if __name__ == "__main__":
    unittest.main()
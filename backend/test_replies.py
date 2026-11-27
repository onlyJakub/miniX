import os
from types import SimpleNamespace
from unittest.mock import patch
from backend.moderation import ModerationError
from backend.test_support import ROOT, DatabaseTestCase

class RepliesTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.execute("DELETE FROM posts")
        self.owner = self.create_and_login().json()
        self.post = self.make_post()
        self.path = f"/api/posts/{self.post['id']}/replies"

    def make_post(self, content="Post z dyskusją"):
        response = self.client.post(
            "/api/posts", json={"content": content, "is_anonymous": True}
        )
        self.assertEqual(response.status_code, 201)
        return response.json()

    def add_reply(self, content="Pierwsza odpowiedź"):
        return self.client.post(self.path, json={"content": content})

    def other_account(self):
        self.client.cookies.clear()
        self.account["username"] += "_other"
        self.account["email"] = "other_" + self.account["email"]
        return self.create_and_login().json()

    def test_create_and_page_replies_chronologically(self):
        self.assertEqual(self.client.get(self.path).json(), {"items": [], "total": 0})
        first = self.add_reply("  Pierwsza 😀  ").json()
        second = self.add_reply("Druga").json()
        self.assertEqual(first["content"], "Pierwsza 😀")
        self.assertEqual(first["author"], {
            "id": self.owner["id"], "username": self.owner["username"]
        })
        self.assertTrue(first["is_owner"])
        self.assertEqual(first["post_id"], self.post["id"])
        self.assertTrue(first["created_at"].endswith("Z"))
        self.assertEqual(self.client.get(self.path).json(), {
            "items": [first, second], "total": 2
        })
        self.assertEqual(self.client.get(f"{self.path}?limit=1&offset=1").json(), {
            "items": [second], "total": 2
        })
        self.assertEqual(self.client.get(f"{self.path}?offset=10").json(), {
            "items": [], "total": 2
        })
        for query in ("limit=0", "limit=51", "offset=-1"):
            self.assertEqual(self.client.get(f"{self.path}?{query}").status_code, 422)

    def test_validates_whitespace_unicode_and_rejects_anonymous_option(self):
        for content in ("", " \n\t\u2003", "ą" * 280 + "😀", "a\x00b"):
            with self.subTest(content=content):
                self.assertEqual(self.add_reply(content).status_code, 422)
        response = self.client.post(self.path, json={"content": "Wpis", "is_anonymous": True})
        self.assertEqual(response.status_code, 422)
        self.assertEqual(self.client.get(self.path).json()["total"], 0)
        self.assertEqual(self.add_reply("ą" * 279 + "😀").status_code, 201)

    def test_missing_post_returns_404_without_asking_model(self):
        unknown = "/api/posts/2147483647/replies"
        with patch.dict(os.environ, {"AI_MODERATION_ENABLED": "true"}), patch(
            "backend.posts.moderate_text"
        ) as classify:
            self.assertEqual(self.client.get(unknown).status_code, 404)
            self.assertEqual(self.client.post(unknown, json={"content": "Tekst"}).status_code, 404)
        classify.assert_not_called()

    def test_requires_session_and_checks_origin_on_write(self):
        payload = {"content": "Odpowiedź"}
        reply = self.add_reply().json()
        reply_path = f"{self.path}/{reply['id']}"
        headers = {"Origin": "https://example.com"}
        self.assertEqual(self.client.post(self.path, json=payload, headers=headers).status_code, 403)
        self.assertEqual(self.client.delete(reply_path, headers=headers).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.client.get(self.path).status_code, 401)
        self.assertEqual(self.client.post(self.path, json=payload).status_code, 401)
        self.assertEqual(self.client.delete(reply_path).status_code, 401)

    def test_reply_owner_can_delete_and_parent_id_must_match(self):
        other_post = self.make_post("Drugi post")
        other = self.other_account()
        reply = self.add_reply().json()
        self.assertEqual(reply["author"]["id"], other["id"])
        path = f"{self.path}/{reply['id']}"
        self.assertEqual(
            self.client.delete(f"/api/posts/{other_post['id']}/replies/{reply['id']}").status_code,
            404,
        )
        self.client.cookies.clear()
        self.assertEqual(self.login(identifier=self.owner["username"]).status_code, 200)
        self.assertFalse(self.client.get(self.path).json()["items"][0]["is_owner"])
        self.assertEqual(self.client.delete(path).status_code, 403)
        self.client.cookies.clear()
        self.assertEqual(self.login().status_code, 200)
        self.assertEqual(self.client.delete(path).status_code, 204)
        self.assertEqual(self.client.get(self.path).json()["total"], 0)
        self.assertEqual(self.client.delete(path).status_code, 404)

    def test_counts_do_not_multiply_likes_and_anonymous_author_stays_hidden(self):
        post_path = f"/api/posts/{self.post['id']}"
        self.assertEqual(self.client.post(post_path + "/like").status_code, 200)
        self.add_reply("Odpowiedź właściciela")
        self.other_account()
        self.assertEqual(self.client.post(post_path + "/like").status_code, 200)
        self.add_reply("Odpowiedź drugiej osoby")
        for path in ("/api/posts", "/api/posts/trending"):
            post = self.client.get(path).json()[0]
            self.assertEqual(post["replies_count"], 2)
            self.assertEqual(post["likes_count"], 2)
            self.assertTrue(post["liked_by_me"])
            self.assertTrue(post["is_anonymous"])
            self.assertIsNone(post["author"])
            self.assertFalse(post["is_owner"])

    def test_replies_are_cascaded_when_post_is_deleted(self):
        self.assertEqual(self.add_reply().status_code, 201)
        self.assertEqual(self.client.delete(f"/api/posts/{self.post['id']}").status_code, 204)
        self.assertEqual(self.client.get(self.path).status_code, 404)
        with self.connect() as connection:
            self.assertEqual(connection.execute("SELECT COUNT(*) AS n FROM replies").fetchone()["n"], 0)

    def test_uses_same_moderation_and_only_safe_reply_is_saved(self):
        with patch.dict(os.environ, {"AI_MODERATION_ENABLED": "true"}), patch(
            "backend.posts.moderate_text"
        ) as classify:
            self.assertEqual(self.add_reply(" ").status_code, 422)
            classify.assert_not_called()
            for decision in ("review", "unsafe", "safe"):
                with self.subTest(decision=decision):
                    classify.return_value = SimpleNamespace(decision=decision)
                    response = self.add_reply(decision)
                    classify.assert_called_with(decision)
                    self.assertEqual(response.status_code, 201 if decision == "safe" else 422)
                    if decision != "safe":
                        self.assertEqual(response.json()["moderation_decision"], decision)
                        self.assertEqual(self.client.get(self.path).json()["total"], 0)
            classify.side_effect = ModerationError("Wewnętrzny błąd LM Studio")
            response = self.add_reply("Błąd serwera modelu")
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("Wewnętrzny", response.text)
        self.assertEqual(self.client.get(self.path).json()["total"], 1)

    def test_when_moderation_disabled_model_is_not_called(self):
        with patch("backend.posts.moderate_text") as classify:
            self.assertEqual(self.add_reply().status_code, 201)
        classify.assert_not_called()

    def test_reply_stores_literal_text_and_uses_authenticated_author(self):
        for field, value in (("user_id", self.owner["id"]), ("author", {"username": "inny"})):
            response = self.client.post(self.path, json={"content": "Wpis", field: value})
            self.assertEqual(response.status_code, 422)
        content = "Zażółć gęślą jaźń 😀\n<b>Znaczniki i 'cytat'</b>"
        response = self.add_reply(content)
        self.assertEqual(response.status_code, 201)
        reply = response.json()
        self.assertEqual(reply["content"], content)
        self.assertEqual(reply["author"]["id"], self.owner["id"])
        self.assertEqual(self.client.get(self.path).json()["items"], [reply])

    def test_additive_migration_preserves_existing_data_and_is_repeatable(self):
        post_path = f"/api/posts/{self.post['id']}"
        self.client.post(post_path + "/like")
        migration = (ROOT / "backend/sql/add_replies.sql").read_text(encoding="utf-8")
        with self.connect() as connection:
            connection.execute("DROP TABLE replies")
            for _ in range(2):
                connection.execute(migration)
        post = self.client.get("/api/posts").json()[0]
        self.assertEqual(post, self.post | {"likes_count": 1, "liked_by_me": True})
        self.assertEqual(self.add_reply().status_code, 201)
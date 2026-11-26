import os
from unittest.mock import patch

from backend.moderation import ModerationError, ModerationResult
from backend.test_support import DatabaseTestCase

REVIEW_MESSAGE = "Ten wpis może wymagać zmian. Popraw treść i spróbuj ponownie."
UNSAFE_MESSAGE = "Ten wpis może naruszać zasady społeczności i nie został opublikowany."
UNAVAILABLE_MESSAGE = "Moderacja jest chwilowo niedostępna. Spróbuj ponownie później."


class PostModerationTests(DatabaseTestCase):
    def setUp(self):
        super().setUp()
        with self.connect() as connection:
            connection.execute("DELETE FROM posts")
        self.enterContext(patch.dict(os.environ, {"AI_MODERATION_ENABLED": "true"}))
        self.classify = self.enterContext(patch("backend.posts.moderate_text"))

    def publish(self, content="Przykładowy wpis", *, is_anonymous=False, headers=None):
        return self.client.post(
            "/api/posts",
            json={"content": content, "is_anonymous": is_anonymous},
            headers=headers,
        )

    def set_decision(self, decision):
        probabilities = {"safe": 0.05, "review": 0.05, "unsafe": 0.05}
        probabilities[decision] = 0.9
        self.classify.return_value = ModerationResult.model_validate(
            {"choice": decision, "confidence": 0.9, "probabilities": probabilities}
        )

    def stored_posts(self):
        with self.connect() as connection:
            return connection.execute(
                "SELECT user_id, content, is_anonymous FROM posts"
            ).fetchall()

    def assert_no_posts(self):
        self.assertEqual(self.stored_posts(), [])
        for path in ("/api/posts", "/api/posts/trending"):
            response = self.client.get(path)
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json(), [])

    def test_safe_post_is_saved_and_visible(self):
        owner = self.create_and_login().json()
        self.set_decision("safe")
        content = "ą" * 279 + "😀"
        response = self.publish(f"  {content}\n")
        self.assertEqual(response.status_code, 201)
        self.classify.assert_called_once_with(content)
        post = response.json()
        self.assertEqual(post["author"], {"id": owner["id"], "username": owner["username"]})
        self.assertEqual(
            self.stored_posts(),
            [{"user_id": owner["id"], "content": content, "is_anonymous": False}],
        )
        for path in ("/api/posts", "/api/posts/trending"):
            self.assertEqual(self.client.get(path).json(), [post])

    def test_review_and_unsafe_posts_never_reach_database_or_feeds(self):
        self.create_and_login()
        for decision, message in (("review", REVIEW_MESSAGE), ("unsafe", UNSAFE_MESSAGE)):
            with self.subTest(decision=decision):
                self.classify.reset_mock()
                self.set_decision(decision)
                response = self.publish()
                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json(), {"message": message, "moderation_decision": decision}
                )
                self.classify.assert_called_once_with("Przykładowy wpis")
                self.assert_no_posts()

    def test_model_error_is_hidden_and_post_is_not_saved(self):
        self.create_and_login()
        self.classify.side_effect = ModerationError("Rozpoznanie: wewnętrzny błąd serwera AI")
        response = self.publish()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.json(), {"message": UNAVAILABLE_MESSAGE})
        self.classify.assert_called_once_with("Przykładowy wpis")
        self.assert_no_posts()

    def test_disabled_moderation_publishes_without_calling_model(self):
        self.create_and_login()
        with patch.dict(os.environ, {"AI_MODERATION_ENABLED": "false"}):
            response = self.publish()
        self.assertEqual(response.status_code, 201)
        self.classify.assert_not_called()
        self.assertEqual(len(self.stored_posts()), 1)

    def test_anonymous_posts_are_moderated_and_keep_the_real_owner(self):
        owner = self.create_and_login().json()
        self.set_decision("unsafe")
        self.assertEqual(self.publish(is_anonymous=True).status_code, 422)
        self.assert_no_posts()

        self.set_decision("safe")
        response = self.publish(is_anonymous=True)
        self.assertEqual(response.status_code, 201)
        self.assertEqual(self.classify.call_count, 2)
        self.assertTrue(response.json()["is_anonymous"])
        self.assertIsNone(response.json()["author"])
        self.assertTrue(response.json()["is_owner"])
        self.assertEqual(
            self.stored_posts(),
            [{"user_id": owner["id"], "content": "Przykładowy wpis", "is_anonymous": True}],
        )

    def test_content_validation_precedes_moderation(self):
        self.create_and_login()
        for content in ("", "  ", "ą" * 280 + "😀"):
            with self.subTest(content=content):
                response = self.publish(content)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(response.json()["errors"][0]["field"], "content")
        self.classify.assert_not_called()
        self.assert_no_posts()

    def test_guest_cannot_call_moderation(self):
        self.assertEqual(self.publish().status_code, 401)
        self.classify.assert_not_called()
        self.assertEqual(self.stored_posts(), [])

    def test_untrusted_origin_cannot_call_moderation(self):
        self.create_and_login()
        response = self.publish(headers={"Origin": "https://example.com"})
        self.assertEqual(response.status_code, 403)
        self.classify.assert_not_called()
        self.assert_no_posts()
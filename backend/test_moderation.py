import os
import unittest
from unittest.mock import patch
import httpx
from backend.moderation import ModerationError, moderate_text

CONTENT = "Dzisiaj jest piękna pogoda!"
URL = "http://host.docker.internal:1234/v1/systemone"
PROBABILITIES = {"safe": 0.8, "review": 0.15, "unsafe": 0.05}

def make_response(answer, status_code=200):
    return httpx.Response(
        status_code,
        request=httpx.Request("POST", URL),
        json={"answers": {"moderation": answer}},
    )

class ModerationTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(
            patch.dict(
                os.environ,
                {
                    "AI_MODERATION_ENABLED": "true",
                    "AI_BASE_URL": "http://host.docker.internal:1234/",
                    "AI_MODEL": "basal-omni-1.0-mini",
                },
            )
        )
        self.post = self.enterContext(patch("backend.moderation.httpx.post"))
        self.answer = {
            "type": "choice",
            "choice": "safe",
            "confidence": 0.9,
            "probabilities": PROBABILITIES,
        }

    def test_classifies_all_three_categories(self):
        for decision in PROBABILITIES:
            with self.subTest(decision=decision):
                probabilities = dict.fromkeys(PROBABILITIES, 0.05)
                probabilities[decision] = 0.9
                self.post.return_value = make_response(
                    self.answer | {"choice": decision, "probabilities": probabilities}
                )
                self.assertEqual(
                    moderate_text(CONTENT).model_dump(),
                    {
                        "decision": decision,
                        "confidence": 0.9,
                        "probabilities": probabilities,
                    },
                )

    def test_sends_systemone_request_with_timeout(self):
        self.post.return_value = make_response(self.answer)
        moderate_text(CONTENT)
        self.post.assert_called_once()
        self.assertEqual(self.post.call_args.args, (URL,))
        request = self.post.call_args.kwargs
        self.assertEqual(request["json"]["model"], "basal-omni-1.0-mini")
        self.assertEqual(request["json"]["state"], CONTENT)
        self.assertEqual(request["json"]["lang"], "pl")
        question = request["json"]["questions"]["moderation"]
        self.assertEqual(question["type"], "choice")
        self.assertEqual(set(question["criteria"]), set(PROBABILITIES))
        self.assertEqual(request["timeout"].connect, 3)
        self.assertEqual(request["timeout"].read, 15)

    def test_disabled_by_default_does_not_contact_model(self):
        del os.environ["AI_MODERATION_ENABLED"]
        with self.assertRaisesRegex(ModerationError, "wyłączona"):
            moderate_text(CONTENT)
        self.post.assert_not_called()

    def test_timeout(self):
        self.post.side_effect = httpx.ReadTimeout("simulated timeout")
        with self.assertRaisesRegex(ModerationError, "limit czasu"):
            moderate_text(CONTENT)

    def test_connection_error(self):
        self.post.side_effect = httpx.ConnectError("simulated connection error")
        with self.assertRaisesRegex(ModerationError, "połączyć"):
            moderate_text(CONTENT)

    def test_http_error(self):
        self.post.return_value = make_response(self.answer, status_code=503)
        with self.assertRaisesRegex(ModerationError, "HTTP 503"):
            moderate_text(CONTENT)

    def test_malformed_json(self):
        self.post.return_value = httpx.Response(
            200, request=httpx.Request("POST", URL), content=b"bad JSON"
        )
        with self.assertRaisesRegex(ModerationError, "JSON"):
            moderate_text(CONTENT)

    def test_missing_result(self):
        self.post.return_value = httpx.Response(
            200, request=httpx.Request("POST", URL), json=self.answer
        )
        with self.assertRaisesRegex(ModerationError, "wynik klasyfikacji"):
            moderate_text(CONTENT)

    def test_missing_fields(self):
        for field in ("choice", "confidence", "probabilities"):
            with self.subTest(field=field):
                answer = self.answer.copy()
                del answer[field]
                self.post.return_value = make_response(answer)
                with self.assertRaisesRegex(ModerationError, "wynik klasyfikacji"):
                    moderate_text(CONTENT)

    def test_invalid_category(self):
        self.post.return_value = make_response(self.answer | {"choice": "unknown"})
        with self.assertRaisesRegex(ModerationError, "wynik klasyfikacji"):
            moderate_text(CONTENT)

    def test_invalid_probabilities(self):
        for probabilities in (
            {"safe": 0.8, "review": 0.2},
            {"safe": 1.1, "review": 0.0, "unsafe": -0.1},
            PROBABILITIES | {"safe": "0.8"},
            {"safe": True, "review": 0, "unsafe": 0},
            PROBABILITIES | {"safe": 0.5},
            PROBABILITIES | {"unknown": 0},
        ):
            with self.subTest(probabilities=probabilities):
                self.post.return_value = make_response(
                    self.answer | {"probabilities": probabilities}
                )
                with self.assertRaisesRegex(ModerationError, "wynik klasyfikacji"):
                    moderate_text(CONTENT)

    def test_invalid_confidence(self):
        self.post.return_value = make_response(self.answer | {"confidence": 1.1})
        with self.assertRaisesRegex(ModerationError, "wynik klasyfikacji"):
            moderate_text(CONTENT)

    def test_invalid_base_url(self):
        os.environ["AI_BASE_URL"] = "localhost:1234"
        with self.assertRaisesRegex(ModerationError, "AI_BASE_URL"):
            moderate_text(CONTENT)
        self.post.assert_not_called()
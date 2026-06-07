import unittest
from urllib.parse import parse_qs, urlparse

from api.feedback import feedback_email_body, failure_body, validate_params
from app.digest import feedback_link, render_html_digest


class HostedFeedbackTests(unittest.TestCase):
    def test_digest_feedback_link_uses_hosted_endpoint_when_configured(self):
        href = feedback_link(
            feedback_email="finn@example.com",
            digest_id="2026-06-05",
            item_number=3,
            rating=5,
            feedback_base_url="https://finn-signal.vercel.app",
        )
        parsed = urlparse(href)
        params = parse_qs(parsed.query)

        self.assertEqual(
            "https://finn-signal.vercel.app/feedback",
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        )
        self.assertEqual(params["digest_id"], ["2026-06-05"])
        self.assertEqual(params["item"], ["3"])
        self.assertEqual(params["rating"], ["5"])

    def test_digest_email_has_clear_rating_and_chat_link(self):
        html = render_html_digest(
            ranked_data={
                "digest_sections": {
                    "top_signals": [
                        {
                            "item_number": 1,
                            "title": "Demo article",
                            "summary": "Useful context.",
                            "source": "Demo",
                            "scores": {"final_score": 8, "finn_relevance": 8, "global_importance": 8},
                            "include_in_digest": True,
                        }
                    ],
                    "skipped_but_noted": [],
                }
            },
            digest_id="demo-2026-06-07",
            feedback_email="finn@example.com",
            feedback_base_url="https://finn-signal.onrender.com",
        )

        self.assertIn("Rate + chat about this digest", html)
        self.assertIn("https://finn-signal.onrender.com/feedback?digest_id=demo-2026-06-07", html)

    def test_endpoint_accepts_valid_plain_feedback_params(self):
        event, error = validate_params(
            {
                "digest_id": ["2026-06-05"],
                "item": ["3"],
                "rating": ["5"],
            }
        )

        self.assertIsNone(error)
        self.assertEqual(
            event,
            {
                "digest_id": "2026-06-05",
                "item_number": 3,
                "rating": 5,
            },
        )

    def test_failure_body_includes_copyable_fallback_feedback(self):
        event = {
            "digest_id": "2026-06-05",
            "item_number": 3,
            "rating": 5,
        }

        body = failure_body(event, RuntimeError("Resend send failed: HTTP 403"))

        self.assertIn("Could not save feedback", body)
        self.assertIn("3:5", body)
        self.assertIn("digest_id: 2026-06-05", feedback_email_body(event))


if __name__ == "__main__":
    unittest.main()

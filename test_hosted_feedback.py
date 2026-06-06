import unittest
from urllib.parse import parse_qs, urlparse

from api.feedback import validate_params
from app.digest import feedback_link


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
            "https://finn-signal.vercel.app/api/feedback",
            f"{parsed.scheme}://{parsed.netloc}{parsed.path}",
        )
        self.assertEqual(params["digest_id"], ["2026-06-05"])
        self.assertEqual(params["item"], ["3"])
        self.assertEqual(params["rating"], ["5"])

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


if __name__ == "__main__":
    unittest.main()

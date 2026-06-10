import unittest

from app.subscriptions import page_has_captcha, prepare_subscription_submission


class SubscriptionTests(unittest.TestCase):
    def test_prepare_subscription_submission_finds_email_form(self):
        prepared = prepare_subscription_submission(
            "https://example.com/newsletter",
            """
            <form action="/subscribe" method="post">
              <input type="hidden" name="list" value="energy">
              <input type="email" name="email">
              <input type="checkbox" name="consent" required value="yes">
              <button>Subscribe</button>
            </form>
            """,
            "amelia+finnsignal@gmail.com",
        )

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.method, "post")
        self.assertEqual(prepared.url, "https://example.com/subscribe")
        self.assertEqual(prepared.data["email"], "amelia+finnsignal@gmail.com")
        self.assertEqual(prepared.data["list"], "energy")
        self.assertEqual(prepared.data["consent"], "yes")

    def test_prepare_subscription_submission_returns_none_without_email_form(self):
        prepared = prepare_subscription_submission(
            "https://example.com",
            "<form><input name='q'></form>",
            "amelia+finnsignal@gmail.com",
        )

        self.assertIsNone(prepared)

    def test_page_has_captcha_detects_human_verification(self):
        self.assertTrue(page_has_captcha("Please verify you are human with hCaptcha."))
        self.assertFalse(page_has_captcha("Subscribe to our weekly brief."))


if __name__ == "__main__":
    unittest.main()

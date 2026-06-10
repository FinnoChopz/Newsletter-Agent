import os
import unittest
from unittest.mock import patch

from app.subscriptions import (
    attempt_newsletter_subscription,
    discover_subscription_links,
    page_has_captcha,
    prepare_subscription_submission,
)


class FakeResponse:
    def __init__(self, url, html):
        self.url = url
        self.html = html.encode("utf-8")

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self):
        return self.html

    def geturl(self):
        return self.url


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

    def test_prepare_subscription_submission_prefers_newsletter_over_search(self):
        prepared = prepare_subscription_submission(
            "https://example.com",
            """
            <form action="/search"><input name="email"><button>Search</button></form>
            <form action="/newsletter" method="post">
              <h2>Subscribe to the newsletter</h2>
              <input type="email" name="email_address">
              <button>Subscribe</button>
            </form>
            """,
            "amelia+finnsignal@gmail.com",
        )

        self.assertIsNotNone(prepared)
        self.assertEqual(prepared.url, "https://example.com/newsletter")
        self.assertEqual(prepared.data["email_address"], "amelia+finnsignal@gmail.com")

    def test_prepare_subscription_submission_skips_login_forms(self):
        prepared = prepare_subscription_submission(
            "https://example.com",
            """
            <form action="/login">
              <input type="email" name="email">
              <input type="password" name="password">
              <button>Sign in</button>
            </form>
            """,
            "amelia+finnsignal@gmail.com",
        )

        self.assertIsNone(prepared)

    def test_discover_subscription_links_scores_newsletter_links(self):
        links = discover_subscription_links(
            "https://example.com",
            """
            <a href="/privacy">Privacy</a>
            <a href="/newsletter">Subscribe to our newsletter</a>
            <a href="/login">Sign in</a>
            """,
        )

        self.assertEqual(links[0], "https://example.com/newsletter")

    def test_page_has_captcha_detects_human_verification(self):
        self.assertTrue(page_has_captcha("Please verify you are human with hCaptcha."))
        self.assertFalse(page_has_captcha("Subscribe to our weekly brief."))

    def test_attempt_newsletter_subscription_rejects_local_urls(self):
        result = attempt_newsletter_subscription(
            "http://127.0.0.1:8000/newsletter",
            "amelia+finnsignal@gmail.com",
        )

        self.assertEqual(result["status"], "manual_required")
        self.assertIn("private or local", result["reason"])

    def test_attempt_newsletter_subscription_follows_static_newsletter_link(self):
        responses = {
            "https://example.com": FakeResponse(
                "https://example.com",
                '<a href="/newsletter">Subscribe to our newsletter</a>',
            ),
            "https://example.com/newsletter": FakeResponse(
                "https://example.com/newsletter",
                '<form action="/join" method="post"><input type="email" name="email"><button>Subscribe</button></form>',
            ),
            "https://example.com/join": FakeResponse(
                "https://example.com/join",
                "Thanks for subscribing. Check your email.",
            ),
        }

        def fake_urlopen(request, timeout=20):
            url = getattr(request, "full_url", request)
            return responses[url]

        previous = os.environ.get("FINN_SIGNAL_SUBSCRIPTION_RESOLVE_HOSTS")
        os.environ["FINN_SIGNAL_SUBSCRIPTION_RESOLVE_HOSTS"] = "false"
        try:
            with patch("app.subscriptions.urlopen", side_effect=fake_urlopen):
                result = attempt_newsletter_subscription(
                    "https://example.com",
                    "amelia+finnsignal@gmail.com",
                )
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_SUBSCRIPTION_RESOLVE_HOSTS", None)
            else:
                os.environ["FINN_SIGNAL_SUBSCRIPTION_RESOLVE_HOSTS"] = previous

        self.assertEqual(result["status"], "submitted")
        self.assertEqual(result["method"], "http_form")
        self.assertEqual(result["form_url"], "https://example.com/join")


if __name__ == "__main__":
    unittest.main()

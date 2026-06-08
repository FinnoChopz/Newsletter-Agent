import base64
import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from google.auth.exceptions import TransportError

from app.gmail_reader import (
    build_newsletter_query,
    execute_gmail_request,
    extract_text_from_payload,
    html_to_text,
    is_retryable_gmail_error,
)


class FakeRequest:
    def __init__(self, execute):
        self._execute = execute

    def execute(self):
        return self._execute()


class GmailReaderTests(unittest.TestCase):
    def test_timeout_is_retryable(self):
        self.assertTrue(is_retryable_gmail_error(TimeoutError("slow read")))

    def test_transport_error_is_retryable(self):
        self.assertTrue(is_retryable_gmail_error(TransportError("network unavailable")))

    def test_execute_gmail_request_retries_timeout(self):
        previous_retries = os.environ.get("FINN_SIGNAL_GMAIL_RETRIES")
        previous_delay = os.environ.get("FINN_SIGNAL_GMAIL_RETRY_SECONDS")
        os.environ["FINN_SIGNAL_GMAIL_RETRIES"] = "2"
        os.environ["FINN_SIGNAL_GMAIL_RETRY_SECONDS"] = "1"
        calls = {"count": 0}

        def execute():
            calls["count"] += 1
            if calls["count"] == 1:
                raise TimeoutError("slow read")
            return {"messages": []}

        try:
            with patch("app.gmail_reader.time.sleep") as sleep, patch("builtins.print"):
                result = execute_gmail_request(lambda: FakeRequest(execute), "test")
        finally:
            if previous_retries is None:
                os.environ.pop("FINN_SIGNAL_GMAIL_RETRIES", None)
            else:
                os.environ["FINN_SIGNAL_GMAIL_RETRIES"] = previous_retries

            if previous_delay is None:
                os.environ.pop("FINN_SIGNAL_GMAIL_RETRY_SECONDS", None)
            else:
                os.environ["FINN_SIGNAL_GMAIL_RETRY_SECONDS"] = previous_delay

        self.assertEqual(result, {"messages": []})
        self.assertEqual(calls["count"], 2)
        sleep.assert_called_once_with(1)

    def test_html_to_text_preserves_anchor_urls(self):
        text = html_to_text('<p><a href="https://example.com/story">Read story</a></p>')

        self.assertIn("Read story [https://example.com/story]", text)

    def test_extract_text_from_payload_prefers_html_with_links(self):
        plain = base64.urlsafe_b64encode(b"Read story").decode("utf-8")
        html = base64.urlsafe_b64encode(
            b'<a href="https://example.com/story">Read story</a>'
        ).decode("utf-8")
        payload = {
            "parts": [
                {"mimeType": "text/plain", "body": {"data": plain}},
                {"mimeType": "text/html", "body": {"data": html}},
            ]
        }

        text = extract_text_from_payload(payload)

        self.assertIn("https://example.com/story", text)

    def test_build_newsletter_query_ignores_display_name_only_senders(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            sources_path = Path(tmpdir) / "sources.yaml"
            sources_path.write_text(
                """
sources:
  - name: Good Source
    enabled: true
    senders:
      - Good Source
      - News Desk <News@Example.com>
      - news@example.com
  - name: Disabled Source
    enabled: false
    senders:
      - disabled@example.com
""",
                encoding="utf-8",
            )

            query = build_newsletter_query(str(sources_path), days=3)

        self.assertIn("newer_than:3d", query)
        self.assertIn("from:news@example.com", query)
        self.assertNotIn("Good Source", query)
        self.assertNotIn("disabled@example.com", query)
        self.assertEqual(query.count("from:news@example.com"), 1)


if __name__ == "__main__":
    unittest.main()

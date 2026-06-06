import os
import unittest
from unittest.mock import patch

from google.auth.exceptions import TransportError

from app.gmail_reader import execute_gmail_request, is_retryable_gmail_error


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


if __name__ == "__main__":
    unittest.main()

import os
import unittest
from unittest.mock import patch

from app.config import get_bcc_recipients, get_feedback_email, get_recipients


class ConfigTests(unittest.TestCase):
    def test_recipients_must_be_configured_for_sending(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, "FINN_SIGNAL_RECIPIENTS"):
                get_recipients()

    def test_configured_recipients_and_bcc_are_used(self):
        with patch.dict(
            os.environ,
            {
                "FINN_SIGNAL_RECIPIENTS": "fmccooe@gmail.com",
                "FINN_SIGNAL_BCC": "amccooe@gmail.com",
            },
            clear=True,
        ):
            self.assertEqual(get_recipients(), ["fmccooe@gmail.com"])
            self.assertEqual(get_bcc_recipients(), ["amccooe@gmail.com"])

    def test_feedback_email_can_fall_back_for_previews(self):
        with patch.dict(os.environ, {}, clear=True):
            self.assertEqual(get_feedback_email(), "you@example.com")


if __name__ == "__main__":
    unittest.main()

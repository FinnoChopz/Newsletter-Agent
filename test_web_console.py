import os
import unittest
from unittest.mock import patch

from web_console import build_oauth_flow


class WebConsoleTests(unittest.TestCase):
    def test_localhost_oauth_allows_insecure_transport(self):
        previous = os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)

        try:
            with patch("web_console.Flow.from_client_secrets_file") as from_file:
                from_file.return_value.redirect_uri = ""
                build_oauth_flow(8787)

            self.assertEqual(os.environ["OAUTHLIB_INSECURE_TRANSPORT"], "1")
        finally:
            if previous is None:
                os.environ.pop("OAUTHLIB_INSECURE_TRANSPORT", None)
            else:
                os.environ["OAUTHLIB_INSECURE_TRANSPORT"] = previous


if __name__ == "__main__":
    unittest.main()

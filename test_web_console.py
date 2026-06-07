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

    def test_render_oauth_can_load_client_config_from_env(self):
        previous = os.environ.get("FINN_SIGNAL_GOOGLE_CLIENT_CONFIG_JSON")
        os.environ["FINN_SIGNAL_GOOGLE_CLIENT_CONFIG_JSON"] = '{"web":{"client_id":"id","client_secret":"secret","auth_uri":"https://accounts.google.com/o/oauth2/auth","token_uri":"https://oauth2.googleapis.com/token"}}'

        try:
            with patch("web_console.Flow.from_client_config") as from_config:
                from_config.return_value.redirect_uri = ""
                build_oauth_flow(8787)

            self.assertTrue(from_config.called)
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_GOOGLE_CLIENT_CONFIG_JSON", None)
            else:
                os.environ["FINN_SIGNAL_GOOGLE_CLIENT_CONFIG_JSON"] = previous


if __name__ == "__main__":
    unittest.main()

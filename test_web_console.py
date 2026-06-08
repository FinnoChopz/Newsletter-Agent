import os
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

from app.profiles import create_profile
from web_console import build_oauth_flow, console_host, parse_site_guide_output, profile_rankings, render_feedback_app


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

    def test_render_port_binds_all_interfaces_by_default(self):
        previous_port = os.environ.get("PORT")
        previous_host = os.environ.pop("FINN_SIGNAL_CONSOLE_HOST", None)
        os.environ["PORT"] = "10000"

        try:
            self.assertEqual(console_host(), "0.0.0.0")
        finally:
            if previous_port is None:
                os.environ.pop("PORT", None)
            else:
                os.environ["PORT"] = previous_port
            if previous_host is not None:
                os.environ["FINN_SIGNAL_CONSOLE_HOST"] = previous_host

    def test_feedback_app_contains_rating_controls_and_chat(self):
        html = render_feedback_app(
            {
                "digest_id": "demo-2026-06-07",
                "items": [
                    {
                        "item_number": 1,
                        "title": "Article One",
                        "summary": "A useful article.",
                        "source": "Demo",
                        "url": "https://example.com/article",
                    }
                ],
            },
            selected_item="1",
            selected_rating="5",
        )

        self.assertIn("Rate this digest", html)
        self.assertIn("Like", html)
        self.assertIn("Not like", html)
        self.assertIn('data-score="5"', html)
        self.assertIn("Ask about articles", html)
        self.assertIn("https://example.com/article", html)
        self.assertIn('value="5" selected', html)

    def test_site_guide_output_filters_highlight_targets(self):
        parsed = parse_site_guide_output(
            '{"answer":"Click Rankings, then Refresh.","targets":["rankings_tab","bad_selector","rankings_refresh"]}'
        )

        self.assertEqual(parsed["answer"], "Click Rankings, then Refresh.")
        self.assertEqual(parsed["targets"], ["rankings_tab", "rankings_refresh"])

    def test_profile_rankings_empty_state_for_new_profile(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                profile = create_profile("Demo", f"demo-{os.getpid()}@example.com")
                rankings = profile_rankings(profile["id"])
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous

        self.assertEqual(rankings["status"], "empty")
        self.assertEqual(rankings["items"], [])

    def test_console_shell_exposes_rankings_and_guide(self):
        html = Path("web/index.html").read_text(encoding="utf-8")

        self.assertIn('data-tab="rankings"', html)
        self.assertIn('id="rankingList"', html)
        self.assertIn('id="guideWidget"', html)
        self.assertIn('name="interests"', html)
        self.assertIn("Save profile", html)


if __name__ == "__main__":
    unittest.main()

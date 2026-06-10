import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch
from pathlib import Path

from app.profiles import create_profile, profile_paths
from web_console import (
    build_oauth_flow,
    console_host,
    ensure_scheduler_for_profile,
    health_payload,
    parse_site_guide_output,
    profile_rankings,
    render_feedback_app,
    scheduler_state,
    send_profile_now,
    start_profile_send,
    source_confirmation_query,
)


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
        self.assertIn("renderAssistantAnswer", html)
        self.assertIn(".answer p", html)
        self.assertNotIn("white-space:pre-wrap", html)

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
        self.assertEqual(rankings["feedback"]["ratings"], {})

    def test_profile_rankings_includes_saved_user_feedback(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                profile = create_profile("Demo", f"reader-{os.getpid()}@example.com")
                paths = profile_paths(profile["id"])
                output_dir = paths.root / "outputs"
                output_dir.mkdir(parents=True, exist_ok=True)
                digest_id = f"{profile['id']}-2026-06-10"

                (output_dir / "latest_digest_manifest.json").write_text(
                    json.dumps(
                        {
                            "digest_id": digest_id,
                            "created_at": "2026-06-10T12:00:00",
                            "items": [{"item_number": 1, "title": "Article One", "source": "Demo"}],
                        }
                    ),
                    encoding="utf-8",
                )
                (output_dir / "latest_scored_items.json").write_text(
                    json.dumps(
                        {
                            "scored_items": [
                                {
                                    "rank": 1,
                                    "item_number": 1,
                                    "title": "Article One",
                                    "source": "Demo",
                                    "summary": "A useful article.",
                                    "include_in_digest": True,
                                    "scores": {"final_score": 8.4},
                                }
                            ]
                        }
                    ),
                    encoding="utf-8",
                )
                (paths.root / "feedback_log.jsonl").write_text(
                    json.dumps(
                        {
                            "created_at": "2026-06-10T12:30:00",
                            "digest_id": digest_id,
                            "raw_feedback": "1:5\nMore AI infrastructure, less market noise.",
                            "parsed_feedback": {
                                "item_ratings": [{"item_number": 1, "rating": 5, "reason": "Useful"}],
                                "style_notes": ["More AI infrastructure, less market noise."],
                            },
                        }
                    )
                    + "\n",
                    encoding="utf-8",
                )

                rankings = profile_rankings(profile["id"])
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous

        self.assertEqual(rankings["status"], "ready")
        self.assertEqual(rankings["items"][0]["user_feedback"]["rating"], 5)
        self.assertEqual(rankings["feedback"]["ratings"]["1"]["title"], "Article One")
        self.assertIn("More AI infrastructure", rankings["feedback"]["notes"][0]["text"])

    def test_console_shell_exposes_rankings_and_guide(self):
        html = Path("web/index.html").read_text(encoding="utf-8")
        app_js = Path("web/app.js").read_text(encoding="utf-8")

        self.assertIn('data-tab="rankings"', html)
        self.assertIn('id="rankingList"', html)
        self.assertIn('id="guideWidget"', html)
        self.assertIn('name="interests"', html)
        self.assertIn('name="subscription_email"', html)
        self.assertIn("Save profile", html)
        self.assertIn('id="storageBanner"', html)
        self.assertIn('id="sourceFilter"', html)
        self.assertIn("Save all recommendations as pending", html)
        self.assertIn("single daily digest", html)
        self.assertIn("Sends exactly one digest now", html)
        self.assertIn('id="schedulerDetails"', html)
        self.assertIn("Hosted scheduler is enabled", app_js)
        self.assertIn("Try subscribe", app_js)
        self.assertIn("Save for later", app_js)
        self.assertIn("Open subscribe page", app_js)
        self.assertIn("Subscribe with", app_js)
        self.assertIn("Check Gmail", app_js)
        self.assertIn("Mark receiving", app_js)
        self.assertIn("Manual signup needed", app_js)
        self.assertIn('data-source-filter="${value}"', app_js)
        self.assertIn("Pending subscription", app_js)
        self.assertIn("No inactive sources.", app_js)
        self.assertIn("Your article ratings", app_js)
        self.assertIn("Your notes", app_js)
        self.assertIn("Your rating", app_js)

    def test_source_confirmation_query_uses_subscription_alias_and_sender(self):
        query = source_confirmation_query(
            {"subscription_email": "amelia+finnsignal@gmail.com"},
            {"senders": ["Digest <news@example.com>"]},
            days=14,
        )

        self.assertIn("newer_than:14d", query)
        self.assertIn("to:amelia+finnsignal@gmail.com", query)
        self.assertIn("deliveredto:amelia+finnsignal@gmail.com", query)
        self.assertIn("from:news@example.com", query)

    def test_scheduler_state_includes_hosted_flag(self):
        with patch("web_console.read_scheduler_state", return_value={"status": "idle"}), patch(
            "web_console.bool_env",
            return_value=True,
        ):
            state = scheduler_state()

        self.assertTrue(state["hosted"])
        self.assertEqual(state["status"], "idle")

    def test_local_enabled_schedule_installs_runner(self):
        profile = {"schedule": {"enabled": True}}

        with patch("web_console.bool_env", return_value=False), patch(
            "web_console.install_launch_agent",
            return_value={"installed": True, "loaded": True, "status": "installed"},
        ) as install:
            state = ensure_scheduler_for_profile(profile)

        self.assertTrue(install.called)
        self.assertTrue(state["installed"])
        self.assertFalse(state["hosted"])

    def test_hosted_schedule_does_not_install_local_runner(self):
        profile = {"schedule": {"enabled": True}}

        with patch("web_console.bool_env", return_value=True), patch(
            "web_console.install_launch_agent",
        ) as install, patch(
            "web_console.launch_agent_status",
            return_value={"installed": False, "loaded": False},
        ):
            state = ensure_scheduler_for_profile(profile)

        self.assertFalse(install.called)
        self.assertTrue(state["hosted"])

    def test_health_fails_when_hosted_scheduler_has_no_heartbeat(self):
        with patch("web_console.storage_status", return_value={"writable": True, "render_runtime": True, "persistent": True}), patch(
            "web_console.bool_env",
            return_value=True,
        ), patch(
            "web_console.read_scheduler_state",
            return_value={},
        ):
            payload, status = health_payload()

        self.assertEqual(status, 503)
        self.assertFalse(payload["ok"])
        self.assertIn("heartbeat", payload["problems"][0])

    def test_health_passes_with_recent_hosted_heartbeat(self):
        with patch("web_console.storage_status", return_value={"writable": True, "render_runtime": True, "persistent": True}), patch(
            "web_console.bool_env",
            return_value=True,
        ), patch(
            "web_console.read_scheduler_state",
            return_value={"status": "idle", "last_heartbeat_at": "2026-06-10T12:00:00"},
        ), patch(
            "web_console.seconds_since",
            return_value=60,
        ):
            payload, status = health_payload()

        self.assertEqual(status, 200)
        self.assertTrue(payload["ok"])

    def test_send_profile_now_records_delivery_result(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                profile = create_profile("Finn", "finn@example.com")

                with patch(
                    "web_console.run_signal_for_profile",
                    return_value={
                        "status": "sent_no_newsletters",
                        "send_result": {"id": "gmail-message-123"},
                    },
                ):
                    send_profile_now(profile["id"])

                saved = profile_rankings(profile["id"])
                state = create_profile("Finn", "finn@example.com")["state"]
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous

        self.assertEqual(saved["status"], "no_newsletters")
        self.assertEqual(state["last_run_status"], "sent_no_newsletters")
        self.assertEqual(state["last_send_message_id"], "gmail-message-123")

    def test_start_profile_send_reports_existing_running_send(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                profile = create_profile("Finn", "finn@example.com")
                paths = profile_paths(profile["id"])
                paths.state.write_text(
                    f'{{"last_run_status":"running","last_send_started_at":"{datetime.now().isoformat(timespec="seconds")}"}}',
                    encoding="utf-8",
                )

                result = start_profile_send(profile["id"])
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous

        self.assertEqual(result["status"], "already_running")


if __name__ == "__main__":
    unittest.main()

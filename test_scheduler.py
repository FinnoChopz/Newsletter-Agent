import unittest
from datetime import datetime
import os
from pathlib import Path
import subprocess
import tempfile
from unittest.mock import patch

from app.profiles import create_profile, profile_paths, read_json
from app.scheduler import (
    is_profile_due,
    launch_agent_plist,
    launch_agent_status,
    mark_scheduler_checked,
    mark_send_failed,
    mark_sent,
    mark_stale_send_if_needed,
    profile_due_decision,
    read_scheduler_state,
    scheduler_timezone_name,
    update_scheduler_state,
)


class SchedulerTests(unittest.TestCase):
    def profile(self, frequency="daily", enabled=True, time="11:00"):
        return {
            "id": "test",
            "schedule": {
                "enabled": enabled,
                "frequency": frequency,
                "time": time,
            },
        }

    def test_daily_profile_due_at_matching_time_once_per_day(self):
        now = datetime(2026, 6, 6, 11, 0)

        self.assertTrue(is_profile_due(self.profile(), state={}, now=now))
        self.assertFalse(
            is_profile_due(
                self.profile(),
                state={"last_sent_on": "2026-06-06"},
                now=now,
            )
        )

    def test_profile_not_due_before_scheduled_time(self):
        now = datetime(2026, 6, 6, 10, 59)

        self.assertFalse(is_profile_due(self.profile(), state={}, now=now))

    def test_daily_profile_catches_up_after_scheduled_time(self):
        now = datetime(2026, 6, 6, 14, 30)

        self.assertTrue(is_profile_due(self.profile(), state={}, now=now))

    def test_weekdays_skips_weekends(self):
        saturday = datetime(2026, 6, 6, 14, 30)
        monday = datetime(2026, 6, 8, 14, 30)

        self.assertFalse(is_profile_due(self.profile("weekdays"), state={}, now=saturday))
        self.assertTrue(is_profile_due(self.profile("weekdays"), state={}, now=monday))

    def test_every_other_day_requires_two_days_since_last_send(self):
        now = datetime(2026, 6, 6, 11, 0)

        self.assertFalse(
            is_profile_due(
                self.profile("every_other_day"),
                state={"last_sent_on": "2026-06-05"},
                now=now,
            )
        )
        self.assertTrue(
            is_profile_due(
                self.profile("every_other_day"),
                state={"last_sent_on": "2026-06-04"},
                now=now,
            )
        )

    def test_profile_with_no_receiving_sources_is_not_due(self):
        now = datetime(2026, 6, 10, 12, 0)
        profile = {
            **self.profile(),
            "gmail_connected": True,
            "source_count": 0,
        }

        decision = profile_due_decision(profile, state={}, now=now)

        self.assertFalse(decision["due"])
        self.assertEqual(decision["reason"], "no_receiving_sources")

    def test_scheduler_timezone_defaults_to_eastern(self):
        previous = os.environ.pop("FINN_SIGNAL_TIMEZONE", None)
        try:
            self.assertEqual(scheduler_timezone_name(), "America/New_York")
        finally:
            if previous is not None:
                os.environ["FINN_SIGNAL_TIMEZONE"] = previous

    def test_scheduler_state_records_checks_failures_and_success(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                profile = create_profile("Amelia", "amelia@example.com")
                now = datetime(2026, 6, 8, 11, 5)

                mark_scheduler_checked("amelia@example.com", now=now)
                mark_send_failed(profile["id"], "Gmail timed out", now=now)
                state = mark_sent("amelia@example.com", now=now)

                saved = read_json(profile_paths(profile["id"]).state, {})
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous

        self.assertEqual(state["last_sent_on"], "2026-06-08")
        self.assertEqual(saved["last_scheduler_check_at"], "2026-06-08T11:05:00")
        self.assertNotIn("last_error", saved)

    def test_stale_running_send_is_marked_failed(self):
        previous_root = os.environ.get("FINN_SIGNAL_USERS_DIR")
        previous_stale = os.environ.get("FINN_SIGNAL_SEND_STALE_SECONDS")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                os.environ["FINN_SIGNAL_SEND_STALE_SECONDS"] = "60"
                profile = create_profile("Amelia", "amelia@example.com")
                now = datetime(2026, 6, 8, 11, 5)
                state = {
                    "last_run_status": "running",
                    "last_send_started_at": "2026-06-08T11:00:00",
                }

                saved = mark_stale_send_if_needed(profile["id"], state, now=now)
        finally:
            if previous_root is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous_root
            if previous_stale is None:
                os.environ.pop("FINN_SIGNAL_SEND_STALE_SECONDS", None)
            else:
                os.environ["FINN_SIGNAL_SEND_STALE_SECONDS"] = previous_stale

        self.assertIsNotNone(saved)
        self.assertEqual(saved["last_run_status"], "failed")
        self.assertIn("still marked running", saved["last_error"])

    def test_launch_agent_uses_project_venv_python_when_available(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            root = Path(tmpdir)
            python_path = root / ".venv" / "bin" / "python"
            python_path.parent.mkdir(parents=True)
            python_path.write_text("", encoding="utf-8")

            plist = launch_agent_plist(root)

        self.assertEqual(plist["ProgramArguments"][0], str(python_path))
        self.assertTrue(plist["ProgramArguments"][1].endswith("run_scheduled_profiles.py"))

    def test_launch_agent_status_reports_loaded_service(self):
        with tempfile.TemporaryDirectory() as tmpdir:
            plist_path = Path(tmpdir) / "com.finn.finnsignal.profiles.plist"
            plist_path.write_text("plist", encoding="utf-8")
            completed = subprocess.CompletedProcess(
                ["launchctl", "print"],
                0,
                stdout="state = running",
                stderr="",
            )

            with patch("app.scheduler.launch_agent_path", return_value=plist_path), patch(
                "app.scheduler.subprocess.run",
                return_value=completed,
            ):
                status = launch_agent_status(tmpdir)

        self.assertTrue(status["installed"])
        self.assertTrue(status["loaded"])
        self.assertEqual(status["status"], "loaded")

    def test_hosted_scheduler_state_is_persisted_under_users_root(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir

                state = update_scheduler_state({"status": "idle"})
                saved = read_scheduler_state()
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous

        self.assertEqual(state["status"], "idle")
        self.assertEqual(saved["status"], "idle")


if __name__ == "__main__":
    unittest.main()

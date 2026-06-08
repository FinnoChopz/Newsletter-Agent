import unittest
from datetime import datetime
import os
import tempfile

from app.profiles import create_profile, profile_paths, read_json
from app.scheduler import is_profile_due, mark_scheduler_checked, mark_send_failed, mark_sent


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


if __name__ == "__main__":
    unittest.main()

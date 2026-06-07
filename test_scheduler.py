import unittest
from datetime import datetime

from app.scheduler import is_profile_due


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


if __name__ == "__main__":
    unittest.main()

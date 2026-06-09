import tempfile
import unittest
from pathlib import Path

from app.profiles import (
    create_profile,
    list_profiles,
    load_profile,
    profile_paths,
    read_yaml,
    read_sources,
    storage_status,
    update_schedule,
    upsert_source,
)


class ProfileTests(unittest.TestCase):
    def test_create_profile_initializes_user_files(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = create_profile(
                "Amelia",
                "amelia@example.com",
                interests="climate tech, food writing\npublic health",
                root=tmp,
            )
            paths = profile_paths(profile["id"], root=tmp)
            preferences = read_yaml(paths.preferences, {})

            self.assertEqual(profile["display_name"], "Amelia")
            self.assertEqual(profile["interests"], ["climate tech", "food writing", "public health"])
            self.assertTrue(paths.meta.exists())
            self.assertTrue(paths.sources.exists())
            self.assertTrue(paths.preferences.exists())
            self.assertEqual(preferences["user"]["name"], "Amelia")
            self.assertEqual(preferences["strong_interests"], ["climate tech", "food writing", "public health"])
            self.assertNotIn("AI agents", preferences["strong_interests"])
            self.assertEqual(read_sources(profile["id"], root=tmp), [])

    def test_upsert_source_replaces_matching_sender(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = create_profile("Amelia", "amelia@example.com", root=tmp)
            upsert_source(
                profile["id"],
                {"name": "Old", "senders": "news@example.com"},
                root=tmp,
            )
            sources = upsert_source(
                profile["id"],
                {"name": "New", "senders": ["news@example.com"]},
                root=tmp,
            )

            self.assertEqual(len(sources), 1)
            self.assertEqual(sources[0]["name"], "New")

    def test_create_profile_updates_existing_email_and_email_lookup_works(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = create_profile(
                "Amelia",
                "amelia@example.com",
                interests="climate tech",
                root=tmp,
            )
            upsert_source(
                profile["id"],
                {"name": "Climate Brief", "senders": "brief@example.com"},
                root=tmp,
            )

            updated = create_profile(
                "Amy",
                "amelia@example.com",
                interests="architecture, public health",
                root=tmp,
            )
            paths = profile_paths(profile["id"], root=tmp)
            preferences = read_yaml(paths.preferences, {})
            loaded_by_email = load_profile("amelia@example.com", root=tmp)

            self.assertEqual(updated["id"], profile["id"])
            self.assertEqual(updated["display_name"], "Amy")
            self.assertEqual(loaded_by_email["id"], profile["id"])
            self.assertEqual(len(list_profiles(root=tmp)), 1)
            self.assertEqual(preferences["strong_interests"], ["architecture", "public health"])
            self.assertEqual(len(read_sources(profile["id"], root=tmp)), 1)

    def test_update_schedule_validates_time_and_frequency(self):
        with tempfile.TemporaryDirectory() as tmp:
            profile = create_profile("Amelia", "amelia@example.com", root=tmp)
            updated = update_schedule(
                profile["id"],
                time="23:15",
                frequency="every_other_day",
                enabled=False,
                root=tmp,
            )

            self.assertEqual(updated["schedule"]["time"], "23:15")
            self.assertEqual(updated["schedule"]["frequency"], "every_other_day")
            self.assertFalse(updated["schedule"]["enabled"])

    def test_list_profiles_ignores_non_profiles(self):
        with tempfile.TemporaryDirectory() as tmp:
            Path(tmp, "misc").mkdir()
            create_profile("Amelia", "amelia@example.com", root=tmp)

            self.assertEqual(len(list_profiles(root=tmp)), 1)

    def test_storage_status_reports_configured_writable_root(self):
        with tempfile.TemporaryDirectory() as tmp:
            status = storage_status(root=tmp)

        self.assertEqual(status["path"], tmp)
        self.assertTrue(status["writable"])
        self.assertTrue(status["root_exists"])
        self.assertEqual(status["error"], "")


if __name__ == "__main__":
    unittest.main()

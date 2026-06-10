import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.manifests import load_manifest, save_manifest
from app.profiles import create_profile, profile_paths, upsert_source
from app.signal_runner import run_signal_for_profile


class SignalRunnerTests(unittest.TestCase):
    def test_profile_manifest_loads_from_persistent_profile_outputs(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                profile = create_profile("Finn", "finn@example.com")
                digest_id = f"{profile['id']}-2026-06-10"
                with patch("app.manifests.MANIFEST_DIR", Path(tmpdir) / "ephemeral" / "manifests"), patch(
                    "app.manifests.LATEST_MANIFEST_PATH",
                    Path(tmpdir) / "ephemeral" / "latest.json",
                ):
                    save_manifest({"digest_id": digest_id, "items": []})
                    for path in (Path(tmpdir) / "ephemeral").glob("**/*.json"):
                        path.unlink()
                    loaded = load_manifest(digest_id)
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous

        self.assertEqual(loaded["digest_id"], digest_id)

    def test_profile_latest_manifest_loads_only_when_digest_id_matches(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                profile = create_profile("Finn", "finn@example.com")
                digest_id = f"{profile['id']}-2026-06-10"
                paths = profile_paths(profile["id"])
                output_dir = paths.root / "outputs"
                output_dir.mkdir(parents=True, exist_ok=True)
                (output_dir / "latest_digest_manifest.json").write_text(
                    json.dumps({"digest_id": digest_id, "items": [{"item_number": 1}]}),
                    encoding="utf-8",
                )
                global_latest = Path(tmpdir) / "stale_global_latest.json"
                global_latest.write_text(
                    json.dumps({"digest_id": "other-profile-2026-06-10", "items": []}),
                    encoding="utf-8",
                )

                with patch("app.manifests.MANIFEST_DIR", Path(tmpdir) / "empty"), patch(
                    "app.manifests.LATEST_MANIFEST_PATH",
                    global_latest,
                ):
                    loaded = load_manifest(digest_id)
                    with self.assertRaises(FileNotFoundError):
                        load_manifest(f"{profile['id']}-2026-06-11")
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous

        self.assertEqual(loaded["digest_id"], digest_id)

    def test_empty_newsletter_run_writes_outputs_and_send_result(self):
        previous = os.environ.get("FINN_SIGNAL_USERS_DIR")
        try:
            with tempfile.TemporaryDirectory() as tmpdir:
                os.environ["FINN_SIGNAL_USERS_DIR"] = tmpdir
                profile = create_profile("Finn", "finn@example.com")
                paths = profile_paths(profile["id"])
                paths.token.write_text("{}", encoding="utf-8")
                output_dir = Path(tmpdir) / "outputs" / profile["id"]
                output_dir.mkdir(parents=True)
                upsert_source(
                    profile["id"],
                    {
                        "name": "Example",
                        "senders": ["news@example.com"],
                        "status": "receiving",
                    },
                )

                with patch("app.signal_runner.fetch_recent_newsletters", return_value=[]), patch(
                    "app.signal_runner.send_email",
                    return_value={"id": "gmail-message-123"},
                ), patch(
                    "app.signal_runner.profile_output_dir",
                    return_value=output_dir,
                ), patch(
                    "app.signal_runner.save_manifest",
                    return_value=Path(tmpdir) / "manifest.json",
                ):
                    result = run_signal_for_profile(profile["id"])

                scored = output_dir / "latest_scored_items.json"
                manifest = output_dir / "latest_digest_manifest.json"
                digest = output_dir / "finn_signal_latest.html"

                self.assertEqual(result["status"], "sent_no_newsletters")
                self.assertEqual(result["send_result"]["id"], "gmail-message-123")
                self.assertTrue(scored.exists())
                self.assertTrue(manifest.exists())
                self.assertTrue(digest.exists())
        finally:
            if previous is None:
                os.environ.pop("FINN_SIGNAL_USERS_DIR", None)
            else:
                os.environ["FINN_SIGNAL_USERS_DIR"] = previous


if __name__ == "__main__":
    unittest.main()

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from app.profiles import create_profile, profile_paths, upsert_source
from app.signal_runner import run_signal_for_profile


class SignalRunnerTests(unittest.TestCase):
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

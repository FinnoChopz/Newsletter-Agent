import json
import tempfile
import unittest
from pathlib import Path

import yaml

from app.feedback import apply_parsed_feedback, clean_reply_text, parse_feedback


class FeedbackTests(unittest.TestCase):
    def test_clean_reply_text_removes_quoted_original(self):
        raw = """1:5
More local models.

On Sat, Jun 6, 2026 at 11:00 AM Finn McCooe <fmccooe@gmail.com> wrote:
> Reply with feedback:
> 1:5, 2:2, 3:4
"""

        self.assertEqual(
            clean_reply_text(raw),
            "1:5\nMore local models.",
        )

    def test_local_parser_extracts_ratings_and_topic_adjustments(self):
        parsed = parse_feedback(
            "1:5, 2:2. More local models. Less routine market updates.",
            use_model=False,
        )

        self.assertEqual(
            [(item["item_number"], item["rating"]) for item in parsed["item_ratings"]],
            [(1, 5), (2, 2)],
        )
        self.assertIn(
            ("local models", 0.15),
            [(item["topic"], item["delta"]) for item in parsed["topic_adjustments"]],
        )
        self.assertIn(
            ("routine market updates", -0.15),
            [(item["topic"], item["delta"]) for item in parsed["topic_adjustments"]],
        )

    def test_apply_feedback_updates_weights_and_logs_event(self):
        manifest = {
            "digest_id": "2026-06-05",
            "items": [
                {
                    "item_number": 1,
                    "title": "Local model breakthrough",
                    "source": "TLDR AI",
                    "topic_tags": ["local models", "AI models"],
                    "scores": {"final_score": 8.0},
                },
                {
                    "item_number": 2,
                    "title": "Routine market update",
                    "source": "Market News",
                    "topic_tags": ["routine market updates"],
                    "scores": {"final_score": 8.0},
                },
            ],
        }
        parsed = parse_feedback(
            "1:5, 2:2. More local models. Less routine market updates.",
            use_model=False,
        )

        with tempfile.TemporaryDirectory() as tmpdir:
            learned_path = Path(tmpdir) / "learned_preferences.yaml"
            log_path = Path(tmpdir) / "feedback_log.jsonl"

            result = apply_parsed_feedback(
                raw_feedback="1:5, 2:2. More local models. Less routine market updates.",
                parsed_feedback=parsed,
                manifest=manifest,
                learned_preferences_path=learned_path,
                feedback_log_path=log_path,
            )
            learned = yaml.safe_load(learned_path.read_text(encoding="utf-8"))
            log_events = [
                json.loads(line)
                for line in log_path.read_text(encoding="utf-8").splitlines()
            ]

        self.assertFalse(result["warnings"])
        self.assertAlmostEqual(learned["topic_weights"]["local models"], 1.21)
        self.assertAlmostEqual(learned["topic_weights"]["AI models"], 1.06)
        self.assertAlmostEqual(learned["topic_weights"]["routine market updates"], 0.73)
        self.assertAlmostEqual(learned["source_weights"]["TLDR AI"], 1.06)
        self.assertAlmostEqual(learned["source_weights"]["Market News"], 0.88)
        self.assertEqual(len(log_events), 1)
        self.assertEqual(log_events[0]["digest_id"], "2026-06-05")

    def test_unknown_item_number_is_ignored_with_warning(self):
        manifest = {"digest_id": "2026-06-05", "items": []}

        with tempfile.TemporaryDirectory() as tmpdir:
            result = apply_parsed_feedback(
                raw_feedback="99:1",
                parsed_feedback=parse_feedback("99:1", use_model=False),
                manifest=manifest,
                learned_preferences_path=Path(tmpdir) / "learned.yaml",
                feedback_log_path=Path(tmpdir) / "log.jsonl",
            )

        self.assertEqual(result["warnings"], ["Ignored rating for unknown item #99."])


if __name__ == "__main__":
    unittest.main()

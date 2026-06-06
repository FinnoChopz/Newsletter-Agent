import unittest

from app.ranking import build_digest_manifest, rank_scored_items


class RankingTests(unittest.TestCase):
    def test_ranking_recomputes_scores_and_assigns_manifest_numbers(self):
        scored = {
            "scored_items": [
                {
                    "title": "Routine market update",
                    "summary": "Markets moved a bit.",
                    "topic_tags": ["markets"],
                    "scores": {
                        "finn_relevance": 4,
                        "global_importance": 3,
                        "novelty": 2,
                        "actionability": 2,
                        "source_quality": 7,
                        "final_score": 10,
                    },
                    "include_in_digest": True,
                },
                {
                    "title": "Local model breakthrough",
                    "summary": "A local model improved.",
                    "topic_tags": ["local models"],
                    "scores": {
                        "finn_relevance": 9,
                        "global_importance": 7,
                        "novelty": 8,
                        "actionability": 8,
                        "source_quality": 8,
                        "final_score": 1,
                    },
                    "include_in_digest": True,
                },
            ]
        }

        ranked = rank_scored_items(
            scored,
            learned_preferences={
                "topic_weights": {"local models": 1.1},
                "source_weights": {},
            },
            max_items=4,
        )
        manifest = build_digest_manifest(
            ranked,
            digest_id="2026-06-05",
            created_at="2026-06-05T12:00:00",
        )

        self.assertEqual(ranked["scored_items"][0]["title"], "Local model breakthrough")
        self.assertEqual(ranked["scored_items"][0]["scores"]["base_score"], 8.15)
        self.assertEqual(ranked["scored_items"][0]["scores"]["final_score"], 8.97)
        self.assertEqual(manifest["items"][0]["item_number"], 1)
        self.assertEqual(manifest["items"][0]["title"], "Local model breakthrough")


if __name__ == "__main__":
    unittest.main()

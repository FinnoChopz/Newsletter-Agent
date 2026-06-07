import unittest

from app.item_metadata import preserve_scored_item_metadata


class ItemMetadataTests(unittest.TestCase):
    def test_preserve_scored_item_metadata_restores_url_and_source(self):
        scored = {
            "scored_items": [
                {
                    "title": "Important story",
                    "summary": "Short scoring summary.",
                    "source": "Unknown",
                }
            ]
        }
        merged = {
            "items": [
                {
                    "title": "Important story",
                    "url": "https://example.com/full-story",
                    "newsletter_name": "Example Daily",
                    "email_sender": "Example <news@example.com>",
                }
            ]
        }

        updated = preserve_scored_item_metadata(scored, merged)
        item = updated["scored_items"][0]

        self.assertEqual(item["url"], "https://example.com/full-story")
        self.assertEqual(item["newsletter_name"], "Example Daily")
        self.assertEqual(item["source"], "Example Daily")


if __name__ == "__main__":
    unittest.main()

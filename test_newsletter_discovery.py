import unittest

from app.newsletter_discovery import (
    normalize_recommendations,
    parse_json_response,
    recommendation_to_source,
)


class NewsletterDiscoveryTests(unittest.TestCase):
    def test_parse_json_response_extracts_json_object(self):
        parsed = parse_json_response('Here you go: {"recommendations": []}')

        self.assertEqual(parsed, {"recommendations": []})

    def test_recommendation_to_source_marks_discovered_items(self):
        recommendations = normalize_recommendations(
            {
                "recommendations": [
                    {
                        "name": "Energy Brief",
                        "description": "Oil and geopolitics.",
                        "why_relevant": "Matches the query.",
                        "subscription_url": "https://example.com",
                        "likely_senders": ["brief@example.com"],
                        "topics": ["oil", "geopolitics"],
                        "confidence": 1.4,
                    }
                ]
            }
        )
        source = recommendation_to_source(recommendations[0])

        self.assertEqual(source["name"], "Energy Brief")
        self.assertEqual(source["senders"], ["brief@example.com"])
        self.assertEqual(source["source_type"], "discovered")
        self.assertEqual(source["status"], "needs_subscription")
        self.assertEqual(recommendations[0]["confidence"], 1.0)


if __name__ == "__main__":
    unittest.main()

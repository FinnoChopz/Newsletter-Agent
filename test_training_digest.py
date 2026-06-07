import unittest

from app.manifests import extract_digest_id
from send_training_digest import build_training_digest


class TrainingDigestTests(unittest.TestCase):
    def test_extract_digest_id_from_training_subject(self):
        digest_id = extract_digest_id(
            "Subject: Re: Finn-Signal Training - training-broad-20260605-120000"
        )

        self.assertEqual(digest_id, "training-broad-20260605-120000")

    def test_build_training_digest_has_manifest_items_and_feedback_links(self):
        digest_id, manifest, html = build_training_digest("broad")

        self.assertTrue(digest_id.startswith("training-broad-"))
        self.assertTrue(manifest["training"])
        self.assertEqual(manifest["scenario"], "broad")
        self.assertGreaterEqual(len(manifest["items"]), 3)
        self.assertIn("More like this", html)
        self.assertIn("Less like this", html)
        self.assertIn("Read full piece", html)
        self.assertIn("https://example.com/local-model-breakthrough", html)


if __name__ == "__main__":
    unittest.main()

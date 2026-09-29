import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace

from review_queue import review_queue
from test_studio_data import imported_store


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        root = Path(self.temp.name)
        self.store = imported_store(root)
        self.jobs = root / "jobs"
        self.jobs.mkdir()
        self.studio = SimpleNamespace(
            store=self.store, app=SimpleNamespace(token="test"),
            assessments=SimpleNamespace(folder=self.jobs, history=lambda key: {
                "timing_verified": False, "blocked": False, "acquisition_status": "prior_acquisition_candidate",
            }),
        )

    def tearDown(self):
        self.temp.cleanup()

    def test_unscored_is_not_faked_as_ready(self):
        result = review_queue(self.studio, {})
        self.assertEqual(result["counts"]["ready"], 0)
        self.assertEqual(result["counts"]["unscored"], 1)
        self.assertEqual(result["items"][0]["status"], "unscored")
        self.assertIsNone(result["items"][0]["target"])
        self.assertEqual(review_queue(self.studio, {"queue": "ready"})["total"], 0)

    def test_ready_and_reviewed_are_separate(self):
        (self.jobs / "job.json").write_text(json.dumps({
            "property_id": "p1", "status": "completed", "prediction": {"condition": "mixed"},
            "review_target": "target", "created_at": "2026-01-01",
        }))
        self.assertEqual(review_queue(self.studio, {"queue": "ready"})["total"], 1)
        self.store.save_review({"kind":"property","id":"p1","expected_revision":0,"reviewer":"Test",
                               "status":"approved","target_fit":"not_target","condition_label":"mixed",
                               "reason":"Not the scope I want"})
        result = review_queue(self.studio, {})
        self.assertEqual(result["counts"]["ready"], 0)
        self.assertEqual(result["counts"]["reviewed"], 1)
        self.assertEqual(result["items"][0]["target"], "target")
        self.assertEqual(result["items"][0]["human_target"], "not_target")

    def test_photo_matching_is_not_a_target_label(self):
        self.studio.assessments.history = lambda key: {"timing_verified": False}
        result = review_queue(self.studio, {"queue": "photo_match"})
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["counts"]["ready"], 0)
        self.assertIsNone(result["items"][0]["human_target"])

    def test_filters_and_errors(self):
        self.assertEqual(review_queue(self.studio, {"search": "missing"})["total"], 0)
        with self.assertRaises(ValueError):
            review_queue(self.studio, {"queue": "bad"})

    def test_last_sale_and_reference_hidden_from_default(self):
        self.studio.assessments.history = lambda key: {"blocked":True,"timing_verified":False,"acquisition_status":"needs_prior_listing"}
        self.assertEqual(review_queue(self.studio,{})["total"],0)
        self.assertEqual(review_queue(self.studio,{"scope":"quarantine"})["total"],1)
        self.studio.assessments.history = lambda key: None
        self.assertEqual(review_queue(self.studio,{})["total"],0)
        self.assertEqual(review_queue(self.studio,{"scope":"reference"})["total"],1)


if __name__ == "__main__":
    unittest.main()

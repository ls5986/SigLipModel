import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from assessments import Assessments
from prompt_lab import PromptLab
from test_prompt_lab import output_for, response_for
from test_studio_data import imported_store


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = imported_store(self.root)
        self.lab = PromptLab(self.root, self.store.property, self.store.image_path,
                             self.store.apply_proposals, lambda: self.fail("Real key must not load"))
        self.service = Assessments(SimpleNamespace(store=self.store, prompts=self.lab), self.root / "jobs")
        self.calls = []

        def model(request):
            self.calls.append(copy.deepcopy(request))
            result = output_for(request)
            result.update(target_fit="target", target_reason="Visible cosmetic scope")
            for image in result["images"]:
                image.update(context="subject", renovation_scope_score=.72)
            return response_for(result)
        self.lab._call_model = model

    def tearDown(self):
        if self.service.thread:
            self.service.thread.join(5)
        self.temp.cleanup()

    def test_preview_no_paid_calls_and_run_single_call_draft(self):
        preview = self.service.preview({"property_id": "p1"})
        self.assertEqual(self.calls, [])
        self.assertEqual(preview["planned_calls"], 1)
        with self.assertRaises(ValueError):
            self.service.start({"id": preview["id"]})
        self.service.start({"id": preview["id"], "approved": True})
        self.service.thread.join(5)
        result = self.service.job(preview["id"])
        self.assertEqual(result["status"], "completed", result.get("error"))
        self.assertEqual(result["review_target"], "unsure")
        self.assertEqual(result["prediction"]["images"][0]["image_id"], "p1:m1")
        self.assertEqual(result["final_sourcing_decision"], "NEEDS_REVIEW")
        self.assertEqual(len(self.calls), 1)
        self.service.start({"id": preview["id"], "approved": True})
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(self.store.property("p1")["property"]["review"].get("target_fit"))

    def test_amenity_scope_score_is_rejected(self):
        preview = self.service.preview({"property_id": "p1"})
        original = self.lab._call_model

        def bad(request):
            response = original(request)
            data = json.loads(response["output"][0]["content"][0]["text"])
            data["images"][0]["context"] = "shared_amenity"
            return response_for(data)
        self.lab._call_model = bad
        self.service.run(preview)
        result = self.service.job(preview["id"])
        self.assertEqual(result["status"], "failed")
        self.assertIn("Non-subject", result["error"])

    def test_photo_era_review_is_separate_and_revision_guarded(self):
        photo = self.store.property("p1")["images"][0]
        import hashlib
        identity = hashlib.sha256(json.dumps([photo["sha256"]]).encode()).hexdigest()
        h = {"evidence_hash": identity, "review": None}
        payload = {"property_id":"p1","decision":"correct_era","reviewer":"Tester",
                   "reason":"Matched original listing and visible pre-work finishes",
                   "expected_revision":0,"evidence_hash":identity}
        with patch.object(self.service, "history", return_value=h):
            saved = self.service.review_era(payload)
        self.assertEqual(saved["revision"], 1)
        self.assertIsNone(self.store.property("p1")["property"]["review"].get("target_fit"))
        h["review"] = saved
        with patch.object(self.service, "history", return_value=h), self.assertRaises(RuntimeError):
            self.service.review_era(payload)

    def test_partial_different_photo_set_cannot_be_confirmed(self):
        with patch.object(self.service, "history", return_value={"evidence_hash":"different","review":None}):
            with self.assertRaisesRegex(ValueError, "differs"):
                self.service.review_era({"property_id":"p1","decision":"correct_era",
                    "reviewer":"Tester","reason":"Looks right","expected_revision":0,"evidence_hash":"different"})

    def test_blocked_listing_refuses_paid_preview_and_start(self):
        preview = self.service.preview({"property_id": "p1"})
        with patch.object(self.service, "history", return_value={"blocked":True,"block_reason":"Later sale"}):
            with self.assertRaisesRegex(ValueError, "Later sale"):
                self.service.preview({"property_id":"p1"})
            with self.assertRaisesRegex(ValueError, "Later sale"):
                self.service.start({"id":preview["id"],"approved":True})
        self.assertEqual(self.calls,[])


if __name__ == "__main__":
    unittest.main()

import unittest
from types import SimpleNamespace
from unittest.mock import patch

from acquisition_policy import audit_evidence, supports_prior
from guarded_jobs import GuardedJobs
from photo_view import effective_photo


class ProvenanceTests(unittest.TestCase):
    def candidate(self, prior_price=True):
        return {"listing":{"ListingKey":"x","StandardStatus":"Closed"},
                "match":{"exact_apn":True,"street_number_matches":True,"unit_conflict":False,
                         "sale_agreements":[{"source_sale":"prior","price_agrees":prior_price,
                                              "minimum_date_gap_days":2},
                                            {"source_sale":"last","price_agrees":True,"minimum_date_gap_days":0}]}}

    def test_resale_price_match_is_not_acquisition(self):
        self.assertFalse(supports_prior(self.candidate(False)))
        self.assertTrue(supports_prior(self.candidate(True)))

    def test_conflicting_duplicate_rows_quarantine_whole_listing(self):
        rows=[{"source_row":i,"source":{"Address":"Test"},"candidate_listing_key":"x",
               "candidates":[self.candidate(prior)]} for i,prior in [(2,True),(3,False)]]
        result=audit_evidence({"properties":rows})
        self.assertEqual(result["listings"]["x"]["status"],"needs_prior_listing")

    def test_human_tags_win_and_incompatible_room_tags_unknown(self):
        image={"room":"kitchen","features":{"dated_kitchen":True},
               "review":{"room":"bathroom","status":"approved","features":{"dated_bathroom":False}},
               "local_model":{"preferences":{"kitchen":{"score":.9}}}}
        resolved=effective_photo(image,{"room":"kitchen","context":"subject","features":{"dated_kitchen":True},
                                       "renovation_scope_score":.8})
        self.assertEqual(resolved["room"],"bathroom")
        self.assertIsNone(resolved["features"]["dated_kitchen"])
        self.assertFalse(resolved["features"]["dated_bathroom"])
        self.assertIsNone(resolved["gpt_scope_score"])
        self.assertIsNone(resolved["local_score"])

    def test_shared_amenities_do_not_retain_positive_scope(self):
        image={"room":"living","review":{"context":"shared_amenity","preference":"target"},
               "features":{"old_flooring":True},"local_model":{"preferences":{"living":{"score":.9}}}}
        result=effective_photo(image,{"room":"living","context":"subject","renovation_scope_score":.8})
        self.assertTrue(all(v is None for v in result["features"].values()))
        self.assertIsNone(result["preference"])
        self.assertIsNone(result["local_score"])
        self.assertIsNone(result["gpt_scope_score"])

    def test_unverified_history_and_non_subject_supervision_are_masked(self):
        job=GuardedJobs.__new__(GuardedJobs)
        job.assessments=SimpleNamespace(history=lambda key:{"timing_verified":False} if key=="historic" else None)
        rows=[{"property_id":"historic","room":"kitchen","features":{"dated_kitchen":True},"preference":"target"},
              {"property_id":"other","photo_context":"shared_amenity","room":"living","features":{"old_flooring":True},"preference":"target"}]
        with patch("guarded_jobs.StudioJobs._snapshot",return_value=(rows,1)):
            masked,_=job._snapshot()
        self.assertTrue(all(r["room"] is None and not r["features"] and r["preference"] is None for r in masked))


if __name__=="__main__":
    unittest.main()

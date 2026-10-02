import unittest
from types import SimpleNamespace
from unittest.mock import patch

from acquisition_policy import audit_evidence, supports_prior, first_sale_policy
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


def dated_candidate(close='2026-04-25', price=True):
    return {'listing':{'ListingKey':'1144525034','StandardStatus':'Closed','CloseDate':close},
            'match':{'exact_apn':True,'street_number_matches':True,'unit_conflict':False,
                'sale_agreements':[{'source_sale':'prior','sale_date':'2026-01-13','recording_date':'2026-04-24','price_agrees':price,'minimum_date_gap_days':1},
                                   {'source_sale':'last','sale_date':'2026-04-27','price_agrees':True,'minimum_date_gap_days':2}]}}


def test_corte_recording_date_does_not_make_april_photos_january_evidence():
    from acquisition_policy import first_sale_policy
    policy=first_sale_policy(dated_candidate())
    assert policy['target_sale_date']=='2026-01-13'
    assert policy['actual_date_gap_days']==102 and not policy['supported']
    assert supports_prior(dated_candidate('2026-01-13',price=False))


def test_first_sale_is_used_when_two_sales_are_close_together():
    candidate=dated_candidate('2026-01-25')
    candidate['match']['sale_agreements'][1]['sale_date']='2026-01-25'
    assert not supports_prior(candidate)
    assert supports_prior({**candidate,'listing':{**candidate['listing'],'CloseDate':'2026-01-13'}})


def test_duplicate_house_rows_share_first_sale_date():
    from acquisition_policy import annotate_first_sales
    early=dated_candidate('2026-01-13')
    late=dated_candidate('2026-04-25')
    late['match']['sale_agreements']=[{'source_sale':'prior','sale_date':'2026-04-25'}]
    rows=[{'group_id':'same-house','candidate':early},{'group_id':'same-house','candidate':late}]
    annotate_first_sales(rows)
    assert rows[1]['first_sale_date']=='2026-01-13'
    assert not supports_prior(rows[1]['candidate'])


def test_corte_later_photos_are_hidden_and_cannot_be_auto_labeled():
    import pytest
    from test_cloud_runtime import Store,MemoryDatabase
    class Corte(Store):
        def _legacy(self,db,ids): return {}
        def _reviews(self,db,ids): return {}
        def _examples(self,db,identifier):
            rows=super()._examples(db,identifier)
            rows[0]['source_snapshot']['mls_candidates']=[{**dated_candidate(),'listing':{**dated_candidate()['listing'],'ListingKey':identifier}}]
            return rows
    store=Corte(MemoryDatabase(),None)
    detail=store.property('house')
    assert detail['historical_source']['blocked']
    assert detail['historical_source']['sale_policy']['target_sale_date']=='2026-01-13'
    assert detail['images']==[] and not detail['capabilities']['autolabel']
    with pytest.raises(ValueError,match='first-sale'):
        store.request_autolabel({'property_id':'house'})


def test_real_close_date_gap_does_not_compete_with_itself_as_another_sale():
    candidate=dated_candidate(close='2026-01-15')
    assert first_sale_policy(candidate)['supported']


def test_single_2026_resale_does_not_replace_2025_acquisition():
    candidate=dated_candidate(close='2025-12-13')
    candidate['match']['sale_agreements'][0]['sale_date']='2025-12-13'
    candidate['match']['sale_agreements'][1]['sale_date']='2026-04-27'
    assert first_sale_policy(candidate)['target_sale_date']=='2025-12-13'
    assert first_sale_policy(candidate)['supported']

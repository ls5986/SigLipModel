import time
from types import SimpleNamespace
from cloud_store import SupabaseStore

def item(key,blocked):
    return dict(id=key,address=key,city='',listing_id=key,image_count=0,blocked=blocked,
                status='unscored',needs_photo_match=True,review_complete=False,
                autolabel_status='not_requested',tagged_photo_count=0,
                evidence_mode='metadata_only',missing_text=True)

def test_all_records_remain_visible_without_becoming_eligible():
    store=SupabaseStore(SimpleNamespace(workspace='isolated-test'),None)
    store._index=(time.monotonic(),[item('matched',False),item('uncertain',True)],
                  {'imported_rows':3,'matched_listings':2,'unresolved_rows':1})
    all_records=store.queue({'scope':'all','queue':'all'})
    assert {i['id'] for i in all_records['items']}=={'matched','uncertain'}
    assert all_records['counts']['source_conflicts']==1
    assert all_records['counts']['verified']==0
    assert all_records['inventory']['unresolved_rows']==1
    assert store.queue({'scope':'acquisitions','queue':'all'})['total']==1
    assert store.queue({'scope':'quarantine','queue':'all'})['items'][0]['id']=='uncertain'
    searched=store.queue({'scope':'all','queue':'all','search':'uncertain'})
    assert searched['total']==1 and searched['counts']['all']==2

import hashlib
import json
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest

from cloud_storage import PrivateStorage
from cloud_store import SupabaseStore
from cloud_runtime import CloudApp
from studio_data import validate_review


def storage(tmp_path, body, *, status=200, limit=1024):
    calls = []
    def request(r):
        calls.append(r)
        return httpx.Response(status, content=body)
    client = httpx.Client(transport=httpx.MockTransport(request))
    return PrivateStorage('a'*20,'test-server-secret',tmp_path,client=client,max_bytes=limit),calls


def test_private_photo_verified_reused_and_corruption_repaired(tmp_path):
    body=b'photo bytes'
    digest=hashlib.sha256(body).hexdigest()
    client,calls=storage(tmp_path,body)
    first=client.get('acq-training-private','photos/photo.jpg',digest)
    assert first.read_bytes()==body
    assert client.get('acq-training-private','photos/photo.jpg',digest)==first
    assert len(calls)==1
    first.write_bytes(b'corrupt')
    assert client.get('acq-training-private','photos/photo.jpg',digest).read_bytes()==body
    assert len(calls)==2
    # A clean cache after process restart can reconstruct from the same remote object.
    other,_=storage(tmp_path/'restart',body)
    assert other.get('acq-training-private','photos/photo.jpg',digest).read_bytes()==body


@pytest.mark.parametrize('status,body,limit',[(403,b'private',1024),(302,b'',1024),(200,b'bad',1024),(200,b'photo bytes',3)])
def test_failure_never_returns_unverified_photo(tmp_path,status,body,limit):
    client,_=storage(tmp_path,body,status=status,limit=limit)
    with pytest.raises(OSError):
        client.get('acq-training-private','photos/photo.jpg',hashlib.sha256(b'photo bytes').hexdigest())
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize('key',['../secret','photos/../../key','https://example.com','photos\\key','/photos/key'])
def test_storage_path_restrictions(tmp_path,key):
    client,calls=storage(tmp_path,b'')
    with pytest.raises(ValueError): client.get('acq-training-private',key,'a'*64)
    assert not calls


def test_cache_bounded(tmp_path):
    client,_=storage(tmp_path,b'abcd')
    old=tmp_path/('b'*64);old.write_bytes(b'1234')
    client.cache_bytes=4
    client.get('acq-training-private','photos/p.jpg',hashlib.sha256(b'abcd').hexdigest())
    assert not old.exists()


def candidate(prior=True):
    return {'listing':{'ListingKey':'house','StandardStatus':'Closed','UnparsedAddress':'Example'},
            'match':{'exact_apn':True,'street_number_matches':True,'unit_conflict':False,
                     'sale_agreements':[{'source_sale':'prior' if prior else 'last','price_agrees':True,'minimum_date_gap_days':1}]}}


class MemoryDatabase:
    workspace='test'
    def __init__(self): self.states={}
    @contextmanager
    def connect(self): yield self
    def state(self,db,kind,key): return self.states.get((kind,key))
    def save(self,db,kind,key,expected,payload):
        previous=self.state(db,kind,key)
        revision=(previous or {}).get('revision',0)
        if revision!=expected: raise RuntimeError('changed')
        result={**payload,'revision':revision+1}
        self.states[kind,key]=result
        return result
    def execute(self,*args): return self


class Store(SupabaseStore):
    prior=True
    def _examples(self,db,identifier):
        if identifier!='house': raise ValueError('Unknown property')
        return [{'listing_key':'house','source_rows':[1],'source_snapshot':{
            'mls_candidates':[candidate(self.prior)],'spreadsheet':{}},'protected_test':True}]
    def _photos(self,db,identifier):
        return [{'image_id':'house:photo','image_sha256':'a'*64,'protected_test':True}]


def payload(**changes):
    return {'kind':'image','id':'house:photo','room':'kitchen','features':{},
            'preference':'target','reviewer':'reviewer','status':'approved','expected_revision':0,**changes}


def property_payload(**changes):
    return {
        'kind':'property','id':'house','reviewer':'reviewer','status':'approved',
        'expected_revision':0,'target_fit':'target','target_score':5,
        'condition_label':'dated','confidence':'high','evidence_source':'both',
        'reason_tags':['Dated kitchen'],'standout_image_ids':['house:photo'],
        'reason':'Strong visible opportunity',**changes,
    }


def test_reviews_persist_across_store_restart_and_reject_stale_save():
    db=MemoryDatabase()
    first=Store(db,None)
    saved=first.save_review(payload())
    assert saved['revision']==1 and not saved['training_allowed']
    second=Store(db,None)
    assert db.state(db,'image','house:photo')==saved
    with pytest.raises(RuntimeError): second.save_review(payload())
    assert second.save_review(payload(expected_revision=1,preference='not_target'))['revision']==2


def test_cloud_property_rating_preserves_evidence_and_rejects_foreign_photos():
    store=Store(MemoryDatabase(),None)
    saved=store.save_review(property_payload())
    assert saved['target_score']==5
    assert saved['standout_image_ids']==['house:photo']
    with pytest.raises(ValueError,match='Standout'):
        Store(MemoryDatabase(),None).save_review(
            property_payload(standout_image_ids=['other:photo'])
        )


def test_era_quarantine_blocks_approvals_and_preserves_labels():
    db=MemoryDatabase();store=Store(db,None)
    saved=store.save_review(payload())
    history=store._history(store._examples(db,'house'),store._photos(db,'house'),None)
    store.review_era({'property_id':'house','decision':'wrong_era','reason':'Later remodel',
                      'reviewer':'reviewer','expected_revision':0,'evidence_hash':history['evidence_hash']})
    with pytest.raises(ValueError,match='quarantined'): store.save_review(payload(expected_revision=1))
    assert db.state(db,'image','house:photo')==saved


def test_resale_cannot_be_approved_as_acquisition():
    store=Store(MemoryDatabase(),None);store.prior=False
    with pytest.raises(ValueError): store.save_review(payload())
    history=store._history(store._examples(None,'house'),store._photos(None,'house'),None)
    with pytest.raises(ValueError,match='Rematch'): store.review_era({'property_id':'house','decision':'correct_era',
        'reviewer':'reviewer','reason':'Looks old','expected_revision':0,'evidence_hash':history['evidence_hash']})


def test_context_cannot_be_bypassed_by_omitting_it():
    db=MemoryDatabase();store=Store(db,None)
    store.save_review(payload(context='shared_amenity',preference=None))
    with pytest.raises(ValueError,match='Non-subject'): store.save_review(payload(expected_revision=1))


def test_legacy_drafts_do_not_become_training_approvals():
    legacy={'room_preferences':{'house:photo':{'room':'kitchen','preference':'target','status':'draft'}}}
    result=Store._review('image','house:photo',legacy,{})
    assert result['preference'] is None


def test_cloud_app_needs_no_original_local_artifacts_and_rejects_local_jobs():
    store=Store(MemoryDatabase(),None)
    store.queue=lambda args:{'counts':{'all':1,'reviewed':0,'photo_match':1},'photo_count':1}
    app=CloudApp(store)
    summary=app.get_studio().get('/api/studio/summary')
    assert summary['storage']=='supabase'
    assert summary['counts']['all']==1 and summary['photo_count']==1
    assert summary['metadata_policy']['model_feature_groups']==9
    with pytest.raises(ValueError,match='No local training'):
        app.get_studio().post('/api/studio/train',{'approved':True})


def test_cloud_and_local_validation_same_rules():
    with pytest.raises(ValueError): validate_review(payload(context='floor_plan'),{'split':'learning'})


def test_cloud_property_projects_legacy_labels_and_photo_era_without_files():
    class Projection(Store):
        def _photos(self,db,identifier):
            return [{**super()._photos(db,identifier)[0], 'context':'unknown',
                     'context_evidence':{'provider_metadata':{'Order':1,'LongDescription':'Community pool'}}}]
        def _legacy(self,db,ids):
            return {'images':{'house:photo':{'room':'outdoor','status':'approved','features':{}}}}
        def _reviews(self,db,ids): return {}
    detail=Projection(MemoryDatabase(),None).property('house')
    assert detail['property']['address']=='Example'
    assert detail['images'][0]['effective']['context']=='shared_amenity'
    assert detail['images'][0]['review']['status']=='approved'
    assert not detail['images'][0]['training_allowed']
    assert detail['historical_source']['timing_verified'] is False
    assert detail['capabilities']['storage']=='supabase'


def test_cloud_api_roundtrip_and_unavailable_database(tmp_path,monkeypatch):
    import threading
    import urllib.request
    import urllib.error
    import review_server
    monkeypatch.setenv('STUDIO_DATA_BACKEND','supabase')
    db=MemoryDatabase();app=CloudApp(Store(db,None))
    server=review_server.create_server(0,app)
    thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
    origin=f'http://127.0.0.1:{server.server_address[1]}'
    try:
        request=urllib.request.Request(origin+'/api/studio/review',data=json.dumps(payload()).encode(),
           headers={'Origin':origin,'X-Review-Token':app.token,'Content-Type':'application/json'})
        with urllib.request.urlopen(request) as response:
            assert json.load(response)['revision']==1
        with pytest.raises(urllib.error.HTTPError) as conflict: urllib.request.urlopen(request)
        assert conflict.value.code==409
        @contextmanager
        def unavailable(): raise OSError('database unavailable');yield
        db.connect=unavailable
        failed=urllib.request.Request(origin+'/api/studio/review',data=json.dumps(payload(expected_revision=1)).encode(),
           headers={'Origin':origin,'X-Review-Token':app.token,'Content-Type':'application/json'})
        with pytest.raises(urllib.error.HTTPError) as error: urllib.request.urlopen(failed)
        assert error.value.code==500
        assert db.states['image','house:photo']['revision']==1
    finally:
        server.shutdown();server.server_close();thread.join()

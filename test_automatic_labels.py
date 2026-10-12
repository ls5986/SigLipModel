import copy
import math

import pytest

from automatic_labels import POLICY, ROOM_PROMPTS, TYPE_PROMPTS, resolve
from cloud_autolabel import process
from cloud_store import SupabaseStore
from cloud_runtime import CloudApp
from studio_data import now
from test_cloud_runtime import MemoryDatabase, Store


def logits(photo_type, room='kitchen'):
    return [10 if k==photo_type else -10 for k in TYPE_PROMPTS]+[
        10 if k==room else -10 for k in ROOM_PROMPTS]


def test_zero_shot_suggestions_abstain_and_do_not_infer_pool_ownership():
    assert resolve(logits('interior'))['room']=='kitchen'
    assert resolve(logits('interior','bathroom'))['room']=='bathroom'
    assert resolve(logits('exterior'))['room']=='exterior'
    assert resolve(logits('floor_plan'))['context']=='floor_plan'
    assert resolve(logits('document'))['context']=='unrelated'
    pool=resolve(logits('pool'))
    assert pool['context']=='unknown' and pool['uncertain']
    uncertain=resolve([0]*(len(TYPE_PROMPTS)+len(ROOM_PROMPTS)))
    assert uncertain['room']=='other' and uncertain['context']=='unknown' and uncertain['uncertain']
    assert uncertain['features']=={} and 'status' not in uncertain
    with pytest.raises(ValueError): resolve([math.nan]*13)


def test_cloud_request_is_idempotent_hash_bound_and_does_not_require_verification():
    db=MemoryDatabase();store=Store(db,None)
    studio=CloudApp(store).get_studio()
    assert studio.post('/api/studio/autolabel',{'property_id':'house'})['status']=='queued'
    assert studio.get('/api/studio/autolabel?id=house')['status']=='queued'
    assert store.request_autolabel({'property_id':'house'})['status']=='queued'
    assert store.request_autolabel({'property_id':'house'})['status']=='queued'
    request=db.states['document','autolabel-request:house']
    assert request['revision']==1 and ('era','house') not in db.states
    assert not store.autolabel_status('house')['worker_online']
    db.states['document','autolabel-worker']={'at':now(),'status':'ready'}
    assert store.autolabel_status('house')['worker_online']
    db.states['document','autolabel-result:house']={'evidence_hash':request['evidence_hash'],'policy':POLICY}
    assert store.request_autolabel({'property_id':'house'})['status']=='completed'
    db.states['document','autolabel-result:house']['policy']='old-policy'
    db.states['document','autolabel-request:house']['status']='completed'
    assert store.request_autolabel({'property_id':'house'})['status']=='queued'


class WorkerStore:
    def __init__(self):
        self.docs={'autolabel-request:p':{'status':'queued','evidence_hash':'hash',
                   'property_id':'p','policy':POLICY,'revision':1}}
        self.detail={'historical_source':{'evidence_hash':'hash'},
                     'images':[{'id':'p:photo','sha256':'a'*64}]}
    def document(self,key): return copy.deepcopy(self.docs.get(key))
    def save_document(self,key,value,expected):
        assert self.docs.get(key,{}).get('revision',0)==expected
        self.docs[key]={**value,'revision':expected+1}
        return copy.deepcopy(self.docs[key])
    def property(self,identifier): return copy.deepcopy(self.detail)
    def image_path(self,identifier): return 'verified-cache-path'


def test_worker_publishes_suggestions_without_touching_human_reviews_or_model_candidate():
    store=WorkerStore()
    class Classifier:
        def classify(self,paths):
            assert paths==['verified-cache-path']
            return [{**resolve(logits('interior')),'backbone_revision':'model-version'}]
    assert process(store,'p',Classifier())
    assert store.docs['autolabel-request:p']['status']=='completed'
    prediction=store.docs['autolabel-result:p']['images'][0]
    assert prediction['sha256']=='a'*64 and prediction['room']=='kitchen'
    assert all(key.startswith('autolabel-') for key in store.docs)
    assert not process(store,'p',Classifier())


def test_worker_never_publishes_stale_or_failed_inference():
    store=WorkerStore()
    class Classifier:
        def classify(self,paths):
            store.detail['historical_source']['evidence_hash']='changed'
            return [resolve(logits('interior'))]
    with pytest.raises(ValueError,match='changed'): process(store,'p',Classifier())
    assert 'autolabel-result:p' not in store.docs
    assert store.docs['autolabel-request:p']['status']=='failed'
    assert 'verified-cache-path' not in store.docs['autolabel-request:p']['error']


def test_worker_does_not_take_over_a_fresh_lease():
    store=WorkerStore();store.docs['autolabel-request:p'].update(status='running',at=now())
    assert not process(store,'p',None)


def test_automatic_results_prefill_rooms_preserve_human_corrections_and_never_enter_training_labels():
    class Projection(Store):
        def _photos(self,db,identifier):
            return [{**super()._photos(db,identifier)[0],'context':'unknown',
                     'context_evidence':{'provider_metadata':{}}}]
        def _legacy(self,db,ids): return {}
        def _reviews(self,db,ids): return db.states
    db=MemoryDatabase();store=Projection(db,None)
    db.states['document','autolabel-result:house']={'policy':POLICY,'images':[
        {**resolve(logits('interior','living')),'image_id':'house:photo','sha256':'a'*64}]}
    image=store.property('house')['images'][0]
    assert image['effective']['room']=='living' and image['effective']['room_source']=='SigLIP suggestion'
    assert image['review'].get('status')!='approved'
    db.states['image','house:photo']={'room':'bedroom','context':'subject','status':'approved','features':{}}
    image=store.property('house')['images'][0]
    assert image['effective']['room']=='bedroom' and image['effective']['room_source']=='Your review'
    db.states.pop(('image','house:photo'))
    db.states['document','autolabel-result:house']['images'][0]['sha256']='b'*64
    assert store.property('house')['images'][0]['effective']['room']=='other'

import json
import pytest
import httpx
from PIL import Image
from openai_labels import OpenAILabels, policy, start_hosted_worker
from pilot import FEATURES
from cloud_autolabel import process
from test_automatic_labels import WorkerStore
from test_cloud_runtime import MemoryDatabase, Store


def setup(monkeypatch,tmp_path, responder=None):
    monkeypatch.setenv('OPENAI_API_KEY','test-secret-never-real')
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','openai')
    paths = [tmp_path/'photo.jpg'];Image.new('RGB',(20,20)).save(paths[0])
    calls=[]
    def respond(request):
        calls.append(request)
        if responder: return responder(request)
        rows=[{'index':0,'room':'kitchen','context':'subject','condition_label':'maintained_original',
            'features':{f:True if f=='dated_kitchen' else None for f in FEATURES},'uncertain':False}]
        return httpx.Response(200,json={'status':'completed','output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'images':rows})}]}]})
    store=WorkerStore()
    client=httpx.Client(transport=httpx.MockTransport(respond))
    return store,OpenAILabels(store,client),paths,calls


def test_image_only_structured_drafts_cached_and_not_approved(monkeypatch,tmp_path):
    store,classifier,paths,calls=setup(monkeypatch,tmp_path)
    rows=classifier.classify(paths)
    assert rows[0]['condition_label']=='maintained_original'
    assert rows[0]['features']['dated_kitchen'] is True
    assert 'status' not in rows[0] and 'preference' not in rows[0]
    assert classifier.classify(paths)==rows and len(calls)==1
    body=json.loads(calls[0].content)
    assert body['store'] is False and body['text']['format']['strict'] is True
    assert body['model']=='gpt-4.1-mini'
    assert body['input'][1]['content'][-1]['image_url'].startswith('data:image/jpeg;base64,')
    assert 'test-secret' not in json.dumps(store.docs)
    assert store.docs[next(k for k in store.docs if k.startswith('autolabel-budget:'))]['calls']==1


def test_failure_is_sanitized_no_retry_and_budget_fail_closed(monkeypatch,tmp_path):
    def fail(request): return httpx.Response(401,json={'error':'test-secret-never-real'})
    store,classifier,paths,calls=setup(monkeypatch,tmp_path,fail)
    with pytest.raises(ValueError) as error: classifier.classify(paths)
    assert 'test-secret' not in str(error.value) and len(calls)==1
    classifier.limit=1
    with pytest.raises(ValueError,match='limit'): classifier.classify(paths)
    assert len(calls)==1


def test_openai_queue_and_room_features_projection(monkeypatch,tmp_path):
    store,classifier,paths,calls=setup(monkeypatch,tmp_path)
    store.docs['autolabel-request:p']['policy']=policy()
    store.image_path=lambda identifier: paths[0]
    assert process(store,'p',classifier)
    assert store.docs['autolabel-result:p']['policy']==policy()
    assert store.docs['autolabel-request:p']['status']=='completed'
    assert not any(k.startswith('image:') for k in store.docs)


def test_worker_does_not_retry_uncertain_paid_call_after_crash(monkeypatch,tmp_path):
    store,classifier,paths,calls=setup(monkeypatch,tmp_path)
    store.docs['autolabel-request:p'].update(policy=policy(),status='running',at='2020-01-01T00:00:00+00:00')
    assert not process(store,'p',classifier)
    assert not calls and store.docs['autolabel-request:p']['status']=='failed'


def test_no_key_leaves_review_available_without_calls(monkeypatch):
    monkeypatch.delenv('OPENAI_API_KEY',raising=False)
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','openai')
    store=WorkerStore()
    assert start_hosted_worker(store) is None
    assert store.docs['autolabel-worker']['status']=='unconfigured'


def test_policy_change_requeues_existing_siglip_result(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','openai')
    db=MemoryDatabase();store=Store(db,None)
    assert store.request_autolabel({'property_id':'house'})['status']=='queued'
    assert db.states['document','autolabel-request:house']['policy']==policy()


def test_photo_condition_requires_explicit_human_save_and_subject_interior():
    from studio_data import validate_review
    payload={'kind':'image','id':'p:photo','reviewer':'QA','status':'approved','room':'kitchen',
        'context':'subject','condition_label':'maintained_original','features':{}}
    result=validate_review(payload,{'split':'learning'})
    assert result['condition_label']=='maintained_original' and result['source']=='human'
    with pytest.raises(ValueError,match='interior'):
        validate_review({**payload,'context':'shared_amenity'},{'split':'learning'})
    with pytest.raises(ValueError,match='condition'):
        validate_review({**payload,'condition_label':'invalid'},{'split':'learning'})


def test_failed_openai_request_requires_explicit_retry(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','openai')
    db=MemoryDatabase();store=Store(db,None)
    store.request_autolabel({'property_id':'house'})
    db.states['document','autolabel-request:house']['status']='failed'
    assert store.request_autolabel({'property_id':'house'})['status']=='failed'
    assert store.request_autolabel({'property_id':'house','retry':True})['status']=='queued'



def test_explicit_unlimited_budget_still_counts_calls(monkeypatch,tmp_path):
    monkeypatch.setenv('STUDIO_OPENAI_MAX_CALLS_PER_DAY','0')
    store,classifier,paths,calls=setup(monkeypatch,tmp_path)
    classifier.reserve_call();classifier.reserve_call()
    assert classifier.limit==0
    assert next(v for k,v in store.docs.items() if k.startswith('autolabel-budget:'))['calls']==2

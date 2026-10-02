import copy
import json
import httpx
import pytest
from PIL import Image
from pilot import FEATURES
from automatic_labels import resolve
from cloud_autolabel import process
from cloud_store import SupabaseStore
from openai_labels import OpenAILabels, hybrid_policy
from photo_selection import selection
from test_automatic_labels import WorkerStore, logits
from test_cloud_runtime import Store, MemoryDatabase


def test_hybrid_room_phase_never_starts_paid_labels_without_test(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    store=WorkerStore()
    store.docs['autolabel-request:p'].update(policy=hybrid_policy(),stage='rooms',mode='rooms')
    class Rooms:
        policy=hybrid_policy(); stage='rooms'
        def classify(self,paths): return [resolve(logits('exterior'))]
    assert process(store,'p',Rooms())
    assert store.docs['autolabel-request:p']['status']=='awaiting_test'
    assert store.docs['autolabel-result:p']['room_labels_complete']
    assert store.docs['autolabel-result:p']['images'][0]['room_source']=='SigLIP'
    class Paid:
        policy=hybrid_policy();stage='features'
        def classify(self,*args,**kwargs): raise AssertionError('No paid labeling without explicit test')
    assert not process(store,'p',Paid())


def test_retry_clears_prior_failure_diagnostic(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    store=WorkerStore()
    store.docs['autolabel-result:p']={'revision':1,'room_labels_complete':True,'images':[
        {'image_id':'p:1','sha256':'a','room':'kitchen','context':'subject'}
    ]}
    store.docs['autolabel-request:p'].update(
        policy=hybrid_policy(),stage='features',mode='test',status='queued',
        label_provider='copilot',
        error='Old failure',error_details={'code':'image_attachment'}
    )
    store.detail['images'][0].update(
        selection={'included':True},
        suggestions=[{'room':'kitchen','context':'subject'}],
        effective={'room':'kitchen'},
    )
    store.docs['autolabel-result:p']['images'][0]['image_id']='p:photo'
    class Paid:
        policy=hybrid_policy();stage='features';paid=True;provider='copilot'
        last_usage={}
        def classify(self,paths,room_tags):
            return [{**room_tags[0],'features':{},'condition_label':'maintained_original'}]
    assert process(store,'p',Paid())
    request=store.docs['autolabel-request:p']
    assert request['status']=='completed'
    assert 'error' not in request and 'error_details' not in request


def test_small_feature_test_preserves_rooms_and_all_unselected_photos(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    store=WorkerStore()
    rows=[{'id':f'p:{i}','sha256':str(i),'selection':{'included':i!=0},
        'suggestions':[{'room':'exterior','context':'subject','image_id':f'p:{i}','sha256':str(i)}]} for i in range(14)]
    store.detail['images']=rows
    store.docs['autolabel-result:p']={'revision':1,'room_labels_complete':True,'images':[r['suggestions'][0] for r in rows]}
    store.docs['autolabel-request:p'].update(policy=hybrid_policy(),stage='features',mode='test')
    calls=[]
    class Paid:
        policy=hybrid_policy();stage='features';paid=True
        last_usage={'input_tokens':10,'output_tokens':5,'total_tokens':15}
        def classify(self,paths,room_tags):
            calls.append((paths,room_tags))
            return [{**tag,'features':{},'condition_label':'unknown'} for tag in room_tags]
    assert process(store,'p',Paid())
    assert len(calls)==2 and sum(len(paths) for paths,tags in calls)==8
    result=store.docs['autolabel-result:p']['images']
    assert len(result)==14 and all(row['room']=='exterior' for row in result)
    request=store.docs['autolabel-request:p']
    assert request['test_photos']==8 and request['usage']['total_tokens']==30
    assert 'condition_label' not in result[0] and 'condition_label' not in result[-1]


def test_hybrid_gpt_schema_cannot_choose_rooms_and_uses_siglip_room(monkeypatch,tmp_path):
    monkeypatch.setenv('OPENAI_API_KEY','fake-test-key')
    store=WorkerStore();path=tmp_path/'a.jpg';Image.new('RGB',(20,20)).save(path)
    def respond(request):
        body=json.loads(request.content)
        fields=body['text']['format']['schema']['properties']['images']['items']['properties']
        assert 'room' not in fields
        row={'index':0,'context':'subject','condition_label':'dated','uncertain':False,
             'features':{f:True if f=='dated_bathroom' else None for f in FEATURES}}
        return httpx.Response(200,json={'status':'completed','usage':{'total_tokens':25},
            'output':[{'type':'message','content':[{'type':'output_text','text':json.dumps({'images':[row]})}]}]})
    classifier=OpenAILabels(store,httpx.Client(transport=httpx.MockTransport(respond)))
    classifier.policy=hybrid_policy()
    result=classifier.classify([path],room_tags=[{'room':'bathroom','context':'subject'}])[0]
    assert result['room']=='bathroom' and result['room_source']=='SigLIP'
    assert result['features']['dated_bathroom'] is True
    assert classifier.last_usage['total_tokens']==25
    classifier.classify([path],room_tags=[{'room':'bathroom','context':'subject'}])
    assert classifier.last_usage is None


def test_hybrid_test_request_is_explicit_and_idempotent(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    db=MemoryDatabase();store=Store(db,None)
    store.request_autolabel({'property_id':'house'})
    request=db.states['document','autolabel-request:house']
    assert request['mode']=='rooms'
    request.update(status='awaiting_test',stage='features')
    db.states['document','autolabel-result:house']={'room_labels_complete':True}
    assert store.request_autolabel({'property_id':'house'})['status']=='awaiting_test'
    assert store.request_autolabel({'property_id':'house','test':True})['status']=='queued'
    assert request['revision']==1
    assert db.states['document','autolabel-request:house']['mode']=='test'
    store.request_autolabel({'property_id':'house','test':True})
    assert db.states['document','autolabel-request:house']['revision']==2


def test_irrelevant_auto_uncheck_does_not_reject_uncertain_or_exterior_photos():
    for context in ('shared_amenity','floor_plan','unrelated'):
        assert not selection(context)['included']
        assert selection(context,{'include_in_similarity':True})['included']
    for context in ('unknown','subject','subject_exterior'):
        assert selection(context)['included']
        assert not selection(context,{'include_in_similarity':False})['included']


def test_checkbox_save_never_approves_room_or_condition_labels():
    class SelectionStore(Store):
        def _legacy(self,db,ids): return {}
    db=MemoryDatabase();store=SelectionStore(db,None)
    result=store.save_photo_selection({'id':'house:photo','included':False,'reviewer':'QA','expected_revision':0})
    assert result['include_in_similarity'] is False
    assert not any(key in result for key in ('status','room','features','condition_label'))
    db.states['image','house:photo'].update(status='approved',room='kitchen',features={'dated_kitchen':True})
    result=store.save_photo_selection({'id':'house:photo','included':True,'reviewer':'QA','expected_revision':1})
    assert result['status']=='approved' and result['room']=='kitchen' and result['features']['dated_kitchen']


def test_selection_override_preserves_imported_human_labels():
    class Imported(Store):
        def _legacy(self,db,ids):
            return {'images':{'house:photo':{'status':'approved','room':'bathroom','features':{'dated_bathroom':True}}}}
    db=MemoryDatabase();store=Imported(db,None)
    result=store.save_photo_selection({'id':'house:photo','included':False,'reviewer':'QA','expected_revision':0})
    assert result['status']=='approved' and result['features']['dated_bathroom']
    assert result['room']=='bathroom' and result['include_in_similarity'] is False


def test_hosted_openai_cannot_claim_local_copilot_test(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    store=WorkerStore()
    store.docs['autolabel-request:p'].update(policy=hybrid_policy(),stage='features',mode='test',label_provider='copilot')
    class Hosted:
        policy=hybrid_policy();stage='features'
        def classify(self,*args,**kwargs): raise AssertionError('Must not spend OpenAI credits for Copilot requests')
    assert not process(store,'p',Hosted())
    assert store.docs['autolabel-request:p']['status']=='queued'


def test_synthetic_photos_never_reach_room_worker(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    store=WorkerStore()
    store.docs['autolabel-request:p'].update(policy=hybrid_policy(),stage='rooms',mode='rooms')
    store.detail['images'][0]['synthetic_evidence']={'excluded':True}
    class Rooms:
        policy=hybrid_policy();stage='rooms'
        def classify(self,*args,**kwargs): raise AssertionError('Synthetic photo must not reach SigLIP')
    assert process(store,'p',Rooms())
    assert store.docs['autolabel-result:p']['images']==[]


def test_full_copilot_batch_labels_more_than_eight_selected_originals(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    store=WorkerStore()
    rows=[{'id':f'p:{i}','sha256':str(i),'selection':{'included':i!=0},
        'synthetic_evidence':{'excluded':i==1},
        'suggestions':[{'room':'exterior','context':'subject','image_id':f'p:{i}','sha256':str(i)}]} for i in range(14)]
    store.detail['images']=rows
    store.docs['autolabel-result:p']={'revision':1,'room_labels_complete':True,'images':[r['suggestions'][0] for r in rows]}
    store.docs['autolabel-request:p'].update(policy=hybrid_policy(),stage='features',mode='all',label_provider='copilot')
    calls=[]
    class Copilot:
        policy=hybrid_policy();stage='features';paid=True;provider='copilot'
        def classify(self,paths,room_tags):
            calls.extend(paths)
            return [{**tag,'features':{},'condition_label':'unknown'} for tag in room_tags]
    assert process(store,'p',Copilot())
    assert len(calls)==12
    request=store.docs['autolabel-request:p']
    assert request['status']=='completed' and request['processed_photos']==12 and request['total_photos']==12
    assert len(store.docs['autolabel-result:p']['images'])==14


def test_full_request_upgrades_queued_rooms_and_completed_tests(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    store=Store(MemoryDatabase(),None)
    store.request_autolabel({'property_id':'house'})
    response=store.request_autolabel({'property_id':'house','label_provider':'copilot','all_photos':True})
    assert response['mode']=='all' and response['stage']=='rooms'
    request=store.document('autolabel-request:house')
    assert request['label_provider']=='copilot' and 'max_photos' not in request
    store.database.states['document','autolabel-result:house']={'revision':1,'room_labels_complete':True,
        'policy':request['policy'],'evidence_hash':request['evidence_hash']}
    store.database.states['document','autolabel-request:house'].update(status='completed',mode='test')
    response=store.request_autolabel({'property_id':'house','label_provider':'copilot','all_photos':True})
    assert response['stage']=='features'
    store.database.states['document','autolabel-request:house'].update(status='completed')
    revision=store.document('autolabel-request:house')['revision']
    assert store.request_autolabel({'property_id':'house','label_provider':'copilot','all_photos':True})['status']=='completed'
    assert store.document('autolabel-request:house')['revision']==revision


def test_full_room_work_advances_to_features_without_click(monkeypatch):
    monkeypatch.setenv('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    store=WorkerStore()
    store.docs['autolabel-request:p'].update(policy=hybrid_policy(),stage='rooms',mode='all',label_provider='copilot')
    class Rooms:
        policy=hybrid_policy();stage='rooms'
        def classify(self,paths): return [resolve(logits('exterior'))]
    assert process(store,'p',Rooms())
    assert store.docs['autolabel-request:p']['status']=='queued'
    assert store.docs['autolabel-request:p']['stage']=='features'

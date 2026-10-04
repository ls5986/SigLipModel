import copy

import pytest

import model_workbench as module
from model_workbench import (
    candidate_history,compare_candidates,dataset_preview,freeze_dataset,
    save_feedback,worker_state,
)
from workbench_worker import process_pending_runs


class Store:
    def __init__(self):
        self.docs={};self.workspace='test'
    def document(self,key):
        return copy.deepcopy(self.docs.get(key))
    def save_document(self,key,value,expected):
        current=self.docs.get(key)
        assert (current or {}).get('revision',0)==expected
        self.docs[key]={**value,'revision':expected+1}
        return copy.deepcopy(self.docs[key])


def property_row(identifier,group,split='train',known=True):
    return {'id':identifier,'group_id':group,'split':split,'known_target':known,
            'label_exclusion':None,'target_origin':'fixture',
            'review':{},'metadata':{},'training_allowed':split!='test'}


def test_dataset_preview_freeze_is_versioned_and_preserves_protected_split(monkeypatch):
    store=Store()
    properties=[
        property_row('positive','g1'),
        property_row('negative','g2'),
        property_row('held','g3','test'),
    ]
    monkeypatch.setattr('cloud_training.snapshot',lambda store:([],properties))
    monkeypatch.setattr(module,'feedback_snapshot',lambda store:{
        'negative':{'target_label':'NOT_TARGET','hard_negative':True,
                    'physical_condition':'C3_WELL_MAINTAINED',
                    'modernization_state':'FULLY_REMODELED',
                    'excluded_photo_ids':[]},
    })
    preview=dataset_preview(store)
    assert preview['counts']['targets']==2
    assert preview['counts']['not_targets']==1
    assert preview['counts']['hard_negatives']==1
    assert not preview['trainable']
    assert preview['reasons']
    assert next(row for row in preview['examples'] if row['property_id']=='held')['split']=='test'
    frozen=freeze_dataset(store,{'id':preview['id'],'confirmed':True})
    assert frozen['dataset_version']=='Dataset V1'
    assert store.document('workbench-dataset-latest')['fingerprint']==preview['fingerprint']
    with pytest.raises(ValueError,match='current dataset preview'):
        freeze_dataset(store,{'id':'missing','confirmed':True})


def test_feedback_requires_explicit_negative_for_hard_negative(monkeypatch):
    store=Store()
    state={'request':{'status':'completed','property_id':'p'},
           'result':{'model':{'version':'v0'},'photos':[{'image_id':'p:1'}]},
           'feedback':None}
    monkeypatch.setattr(module,'run_status',lambda store,identifier:state)
    with pytest.raises(ValueError,match='Hard negative'):
        save_feedback(store,{'run_id':'a'*32,'target_label':'TARGET',
            'hard_negative':True,'physical_condition':'UNKNOWN',
            'modernization_state':'UNKNOWN','reviewer':'R'})
    saved=save_feedback(store,{'run_id':'a'*32,'target_label':'NOT_TARGET',
        'hard_negative':True,'physical_condition':'C3_WELL_MAINTAINED',
        'modernization_state':'FULLY_REMODELED','reviewer':'R',
        'excluded_photo_ids':['p:1'],'reason':'Turnkey false positive'})
    assert saved['hard_negative'] and saved['target_label']=='NOT_TARGET'


def test_pending_run_saves_result_without_retraining():
    store=Store()
    request={'id':'b'*32,'property_id':'p','status':'queued',
             'requested_mode':'automatic','revision':1}
    store.docs['workbench-run:'+request['id']]=request
    class Database:
        class Context:
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def execute(self,*args):
                class Result:
                    def fetchall(inner): return [{'payload':request}]
                return Result()
        def connect(self): return self.Context()
    store.database=Database()
    class Scorer:
        def score(self,*args):
            return {'property_id':'p','prediction':{'score':.8},
                    'photos':[],'condition':{},'evidence':{},
                    'model':{'version':'v0'},'policy':'workbench-result-v1'}
    assert process_pending_runs(store,Scorer())==1
    assert store.document('workbench-run:'+request['id'])['status']=='completed'
    assert store.document('workbench-result:'+request['id'])['model']['version']=='v0'


def test_worker_heartbeat_live_stale_and_stopped(monkeypatch):
    store=Store()
    store.docs['workbench-worker']={
        'status':'ready','at':'2026-01-01T00:00:00+00:00','detail':{'model_version':'v0'},
        'revision':1,
    }
    class Clock:
        @classmethod
        def now(cls,tz): return __import__('datetime').datetime(2026,1,1,0,0,30,tzinfo=tz)
        @classmethod
        def fromisoformat(cls,value): return __import__('datetime').datetime.fromisoformat(value)
    monkeypatch.setattr(module,'datetime',Clock)
    assert worker_state(store)['actionable']
    assert worker_state(store)['can_train']
    assert worker_state(store)['can_score']
    assert not worker_state(store,threshold_seconds=20)['online']
    store.docs['workbench-worker']['status']='stopped'
    assert worker_state(store)['status']=='stopped'
    assert not worker_state(store)['actionable']


def test_training_only_render_worker_does_not_accept_score_runs(monkeypatch):
    store=Store()
    store.docs['workbench-worker']={
        'status':'ready','at':'2026-01-01T00:00:00+00:00',
        'detail':{'mode':'training_only_until_first_v1_candidate'},'revision':1,
    }
    class Clock:
        @classmethod
        def now(cls,tz): return __import__('datetime').datetime(2026,1,1,0,0,30,tzinfo=tz)
        @classmethod
        def fromisoformat(cls,value): return __import__('datetime').datetime.fromisoformat(value)
    monkeypatch.setattr(module,'datetime',Clock)
    state=worker_state(store)
    assert state['actionable'] and state['can_train']
    assert not state['can_score']


def test_candidate_history_is_immutable_and_comparison_explains_v0_boundary():
    store=Store()
    store.docs['model-baseline:v0-positive-similarity']={
        'version':'v0','metrics':{'positive_similarity':True},'revision':1,
    }
    candidates=[
        {'payload':{
            'id':'v1b','version':'V1 B','dataset_version':'Dataset V2',
            'artifact_sha256':'b'*64,'metrics':{'protected_test':{'fusion':{
                'balanced_accuracy':.7,'brier':.2,
            }}},
        }},
        {'payload':{
            'id':'v1a','version':'V1 A','dataset_version':'Dataset V1',
            'artifact_sha256':'a'*64,'metrics':{'protected_test':{'fusion':{
                'balanced_accuracy':.6,'brier':.3,
            }}},
        }},
    ]
    class Database:
        class Context:
            def __enter__(self): return self
            def __exit__(self,*args): return None
            def execute(self,*args):
                class Result:
                    def fetchall(inner): return copy.deepcopy(candidates)
                return Result()
        def connect(self): return self.Context()
    store.database=Database()
    history=candidate_history(store)
    assert [item['id'] for item in history['items']]==[
        'v0-positive-similarity','v1b','v1a',
    ]
    boundary=compare_candidates(store,{
        'left':'v0-positive-similarity','right':'v1b',
    })
    assert not boundary['comparable']
    assert 'not classifier metrics' in boundary['notice']
    comparison=compare_candidates(store,{'left':'v1a','right':'v1b'})
    assert comparison['comparable']
    accuracy=next(
        row for row in comparison['metrics']
        if row['metric']=='balanced_accuracy'
    )
    assert accuracy['delta']==pytest.approx(.1)

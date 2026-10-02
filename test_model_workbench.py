import copy

import pytest

import model_workbench as module
from model_workbench import dataset_preview, freeze_dataset, save_feedback
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

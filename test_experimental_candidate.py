from copy import deepcopy
import numpy as np
import pytest
from scipy.sparse import csr_matrix
from experimental_candidate import train,predict_head,metadata_matrix,select_rows,enqueue,REQUEST
from studio_v2 import label_evidence
from typed_label_assistant import POLICY
from condition_schema import LABEL_SCHEMA_V2

class Encoder:
 def transform(self,texts):return csr_matrix([[1.,0.] if 'original' in t else [0.,1.] for t in texts])
def rows():
 return [{'id':str(i),'group_id':str(i),'origin':'unreviewed_ai_draft','labels':{'physical_condition':'C4_AVERAGE_FUNCTIONAL' if i%2 else 'C3_WELL_MAINTAINED','modernization':'ORIGINAL' if i%2 else 'UPDATED','acquisition_fit':'TARGET' if i%2 else 'NOT_TARGET'},'remarks':'original home' if i%2 else 'updated home','metadata':{'YearBuilt':1980 if i%2 else 2020,'City':'train' if i<24 else 'heldout'},'evidence_id':'x','proposal_id':str(i),'review_revision':0,'split':'train' if i<24 else 'validation'} for i in range(30)]

def test_supervised_serialized_heads_holdout_vocabulary_and_honest_metrics():
 sample=rows();sample[24]['labels']['physical_condition']='UNKNOWN'
 bundle=train(sample,{'protected_group':4},Encoder(),{'revision':'pinned'})
 assert bundle['counts']['train_groups']==24
 assert 'City=heldout' not in bundle['metadata_names']
 assert bundle['evaluation']['physical_condition:unreviewed_ai_draft']['groups']==5
 assert 'NOT human accuracy' in bundle['evaluation']['physical_condition:unreviewed_ai_draft']['meaning']
 matrix,_,_=metadata_matrix(sample[:24],bundle['metadata_names'],bundle['metadata_scales'])
 probs=predict_head(bundle['heads']['physical_condition'],np.column_stack([Encoder().transform([r['remarks'] for r in sample[:24]]).toarray(),matrix]))
 assert probs.shape==(24,2);assert np.allclose(probs.sum(axis=1),1)
 assert set(bundle['heads']['physical_condition']['training_class_counts'])=={'C4_AVERAGE_FUNCTIONAL','C3_WELL_MAINTAINED'}
 assert all(r['origin']=='unreviewed_ai_draft' for r in bundle['provenance'])

def test_unknown_and_one_class_do_not_create_heads():
 sample=rows()
 for r in sample:r['labels']={k:'UNKNOWN' for k in r['labels']}
 with pytest.raises(ValueError,match='two known classes'):train(sample,{},Encoder(),{})
 with pytest.raises(ValueError,match='20 independent'):train(sample[:3],{},Encoder(),{})

def test_rare_class_does_not_block_supported_classes_or_become_negative():
 sample=rows();sample[0]['labels']['physical_condition']='C6_SEVERE_DISTRESS'
 bundle=train(sample,{},Encoder(),{})
 head=bundle['heads']['physical_condition']
 assert set(head['classes'])=={'C3_WELL_MAINTAINED','C4_AVERAGE_FUNCTIONAL'}
 assert head['unsupported_class_counts']=={'C6_SEVERE_DISTRESS':1}
 assert sum(head['training_class_counts'].values())==23

class Store:
 def __init__(self):self.docs={};self.details={}
 def document(self,key):return deepcopy(self.docs.get(key))
 def save_document(self,key,payload,revision):
  assert (self.docs.get(key) or {}).get('revision',0)==revision
  self.docs[key]={**deepcopy(payload),'revision':revision+1};return self.document(key)
 def property(self,id):return deepcopy(self.details[id])

def test_source_protection_human_priority_and_group_conflicts():
 s=Store();props=[]
 for id in ('human','alias','protected','blocked','stale'):
  prop={'id':id,'mls_remarks':'original kitchen','metadata':{},'review':{}}
  detail={'property':prop,'images':[],'historical_source':{'blocked':id=='blocked'}};s.details[id]=detail
  evidence=label_evidence(id,prop['mls_remarks'],[],{})
  label={'status':'draft','policy':POLICY,'proposal_id':id,'label_evidence_id':evidence,'physical_condition':'C4_AVERAGE_FUNCTIONAL','modernization_state':'ORIGINAL','acquisition_fit':'TARGET','text_signals':[]}
  s.docs['typed-label-result:'+id]=label
  props.append({'id':id,'group_id':'same' if id in ('human','alias') else id,'split':'test' if id=='protected' else 'train','timing_verified':True})
 s.details['human']['property']['review']={**s.docs['typed-label-result:human'],'status':'approved','label_schema_version':LABEL_SCHEMA_V2,'reviewer':'actual-human','physical_condition':'C5_REHAB_NEEDED','target_fit':'not_target','revision':3}
 s.docs['typed-label-result:stale']['label_evidence_id']='old'
 selected,excluded=select_rows(s,props)
 assert len(selected)==1;assert selected[0]['id']=='human';assert selected[0]['origin']=='approved_human';assert selected[0]['labels']['acquisition_fit']=='NOT_TARGET'
 assert excluded=={'protected_group':1,'source_era':1,'no_current_draft':1}
 assert s.docs['typed-label-result:alias']['status']=='draft'
 # Supported text/facts remain usable without manually certified photographs.
 props[1]['timing_verified']=False;props[1]['label_exclusion']='Photo era pending'
 props[1]['text_source_valid']=True
 selected,_=select_rows(s,[props[1]])
 assert selected[0]['id']=='alias' and selected[0]['photo_era_verified'] is False
 assert s.details['alias']['property']['review']=={}
 props[1]['text_source_valid']=False
 assert select_rows(s,[props[1]])==([] ,{'source_era':1})

def test_explicit_idempotent_training_does_not_approve_truth():
 s=Store()
 with pytest.raises(ValueError):enqueue(s,{'confirmed':True},'admin')
 first=enqueue(s,{'confirmed':True,'include_unreviewed_drafts':True},'admin')
 assert enqueue(s,{'confirmed':True,'include_unreviewed_drafts':True},'admin')['request']['id']==first['request']['id']
 assert list(s.docs)==[REQUEST]

def test_worker_recovers_completion_after_bundle_saved_before_restart():
 from experimental_candidate import poll_training
 s=Store();s.docs[REQUEST]={'id':'saved','status':'running','revision':2,'at':'2026-01-01T00:00:00+00:00'}
 s.docs['experimental-candidate:saved']={'id':'saved','heads':{}}
 assert poll_training(s) is True
 assert s.document(REQUEST)['status']=='completed'
 assert s.document(REQUEST)['revision']==3

def test_active_training_lease_does_not_run_a_second_fit():
 from experimental_candidate import poll_training
 from studio_data import now
 s=Store();s.docs[REQUEST]={'id':'active','status':'running','revision':2,'at':now()}
 assert poll_training(s) is False
 assert s.document(REQUEST)['revision']==2

def test_source_only_confirmation_without_typed_labels_never_loads_property():
 s=Store()
 selected,excluded=select_rows(s,[{'id':'source-only','group_id':'g','split':'train','timing_verified':True}])
 assert selected==[] and excluded=={'no_current_draft':1}


def test_reference_listing_prediction_does_not_remove_training_quarantine():
 from experimental_candidate import queue_prediction,prediction
 s=Store();s.details['reference']={'property':{'id':'reference','mls_remarks':'Updated home','metadata':{}},'images':[],'historical_source':{'blocked':True}}
 s.docs[REQUEST]={'id':'candidate','status':'completed'}
 s.docs['experimental-candidate:candidate']={'id':'candidate','created_at':'fixture','policy':'fixture','counts':{},'evaluation':{},'encoder':{},'dataset_fingerprint':'fixture','limitations':[],'heads':{}}
 queued=queue_prediction(s,{'id':'reference'},'reviewer')
 assert queued['status']=='queued'
 assert prediction(s,'reference')['status']=='queued'
 assert s.details['reference']['historical_source']['blocked'] is True
 assert not any(k.startswith('typed-label-result:') for k in s.docs)

from contextlib import contextmanager
from copy import deepcopy
import pytest
from typed_draft_batch import create, advance, status, KEY, budget_available
from typed_label_assistant import evidence, POLICY
class Store:
 workspace='test'
 def __init__(self):self.docs={};self.database=self
 def document(self,key):return deepcopy(self.docs.get(key))
 def save_document(self,key,value,revision):
  assert (self.docs.get(key) or {}).get('revision',0)==revision
  self.docs[key]={**deepcopy(value),'revision':revision+1};return self.document(key)
 @contextmanager
 def connect(self):yield self
 def execute(self,sql,args):
  self.rows=[{'item_id':key,'payload':deepcopy(value)} for key,value in self.docs.items() if key in args[1]];return self
 def fetchall(self):return self.rows
 def queue(self,args):return {'items':[{'id':i} for i in ['reuse','blocked','new','missing']][args['offset']:args['offset']+args['limit']],'total':4}
 def property(self,id):
  if id=='missing':raise ValueError('Unavailable')
  return {'property':{'id':id,'mls_remarks':'Real remarks','metadata':{},'review':{'status':'approved','revision':4}},'images':[], 'historical_source':{'blocked':id=='blocked'}}

def test_resume_reuse_source_gate_and_preserved_truth():
 s=Store();identity=evidence(s.property('reuse'));s.save_document('typed-label-result:reuse',{'label_evidence_id':identity,'policy':POLICY},0)
 first=create(s,{'confirmed':True,'selection':'all'},'reviewer');assert first['total']==4
 assert advance(s)==1;assert s.docs[KEY]['entries']=={'reuse':'reused','blocked':'blocked_source','new':'queued'}
 assert s.docs['typed-label-request:new']['requested_by']=='reviewer';assert s.docs['typed-label-request:new']['status']=='queued'
 assert not any(k.startswith('property:') or 'train' in k for k in s.docs)
 assert create(s,{'confirmed':True,'selection':'all'},'reviewer')['id']==first['id'],'repeated clicks reuse active batch'
 s.save_document('typed-label-request:new',{**s.document('typed-label-request:new'),'status':'completed'},1)
 assert advance(s)==0;result=status(s);assert result['status']=='finished';assert result['counts']=={'reused':1,'blocked_source':1,'completed':1,'unavailable':1}
 assert s.property('new')['property']['review']['revision']==4

def test_running_request_never_requeued():
 s=Store();s.save_document('typed-label-request:new',{'status':'running'},0)
 create(s,{'confirmed':True,'selection':'ids','ids':['new']},'reviewer');assert advance(s)==0
 assert s.docs['typed-label-request:new']['revision']==1;assert status(s)['status']=='running'

def test_explicit_batch_confirmation_and_bound():
 s=Store()
 for payload in [{'selection':'all'},{'confirmed':True,'selection':'ids','ids':['a']*501}]:
  with pytest.raises(ValueError):create(s,payload,'reviewer')
 assert not s.docs

def test_budget_stays_queued_without_retry():
 from datetime import datetime,timezone
 from types import SimpleNamespace
 s=Store();key='autolabel-budget:'+datetime.now(timezone.utc).date().isoformat();s.save_document(key,{'calls':500},0)
 assert not budget_available(s,SimpleNamespace(limit=500))
 assert budget_available(s,SimpleNamespace(limit=0))

from copy import deepcopy
import pytest
from sample_selection import change, listing

class Store:
 def __init__(self): self.doc={}; self.writes=0
 def document(self,key): return deepcopy(self.doc) or None
 def save_document(self,key,payload,revision):
  assert revision==self.doc.get('revision',0)
  self.doc={**deepcopy(payload),'revision':revision+1};self.writes+=1
class Studio:
 def __init__(self):self.store=Store();self.requests=[]
 def get(self,path):
  self.requests.append(path)
  if 'missing' in path:raise ValueError('Property is not imported')
  return {'property':{'id':'123','address':'Real retained listing'},'historical_source':{'blocked':True},'images':[]}

def test_sample_selection_is_not_truth_or_split_change():
 s=Studio();assert listing(s.store)['revision']==0
 result=change(s,{'action':'add','id':'123','expected_revision':0},'reviewer')
 assert result['items'][0]['id']=='123';assert result['revision']==1
 assert all(k not in result['items'][0] for k in ('target_fit','split','status','timing_verified'))
 assert s.requests==['/api/studio/v2/property?id=123']
 with pytest.raises(RuntimeError):change(s,{'action':'remove','id':'123','expected_revision':0},'reviewer')
 assert len(listing(s.store)['items'])==1
 assert change(s,{'action':'remove','id':'123','expected_revision':1},'reviewer')['items']==[]

def test_unknown_listing_never_created():
 s=Studio()
 with pytest.raises(ValueError):change(s,{'action':'add','id':'missing','expected_revision':0},'reviewer')
 assert s.store.writes==0

@pytest.mark.parametrize('revision',[True,-1,None])
def test_revision_guard(revision):
 s=Studio()
 with pytest.raises(ValueError):change(s,{'action':'add','id':'123','expected_revision':revision},'reviewer')
 assert s.store.writes==0

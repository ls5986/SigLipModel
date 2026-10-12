import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
import pytest
import paired_dev_package as p

class Storage:
    def __init__(self,root):self.root=root;self.data={}
    def put(self,bucket,key,data,**kwargs):
        if key in self.data:raise FileExistsError()
        self.data[key]=data
        return {'sha256':hashlib.sha256(data).hexdigest()}
    def get(self,bucket,key,digest,**kwargs):
        data=self.data[key]
        assert hashlib.sha256(data).hexdigest()==digest
        path=self.root/digest;path.write_bytes(data);return path

def fixture(monkeypatch,tmp_path):
    b={'candidate_id':p.CANDIDATE,'schema_version':'actvision-paired-condition-v1',
       'dataset_sha256':p.DATASET_SHA,'dataset_id':'dataset',
       'heads':{name:{'weights':[1]} for name in ['vision','text','structured','fusion']},
       'encoders':{'vision_revision':'pinned','text':{'checkpoint_sha256':'checkpoint'}},'code_commit':'commit'}
    blob=json.dumps(b).encode();monkeypatch.setattr(p,'EXPECTED_SHA',hashlib.sha256(blob).hexdigest())
    storage=Storage(tmp_path);storage.data['candidate']=blob
    docs={'paired-condition-candidate:'+p.CANDIDATE:{'artifact_key':'candidate'}}
    def save(key,payload,revision):docs[key]={**payload,'revision':revision+1}
    return SimpleNamespace(storage=storage,document=lambda key:docs.get(key),save_document=save),blob

def test_corrupted_candidate_rejected(monkeypatch,tmp_path):
    _,blob=fixture(monkeypatch,tmp_path)
    with pytest.raises(ValueError,match='checksum'):p.validate_candidate(blob+b' ')

def test_package_resumes_and_preserves_dev_boundary(monkeypatch,tmp_path):
    store,blob=fixture(monkeypatch,tmp_path)
    store.storage.data[p.PREFIX+'candidate.json']=blob
    result=p.package(store)
    assert result['status']=='ready' and result['artifact_count']==6
    assert not result['production_ready'] and not result['inference_smoke_test_passed']
    manifest=json.loads(store.storage.data[result['manifest_key']])
    assert manifest['training_code_commit']=='commit'
    assert manifest['evidence_policy_version']==p.POLICY
    assert len(manifest['artifacts'])==6
    assert p.package(store)['manifest_sha256']==result['manifest_sha256']

def test_resume_rejects_different_existing_bytes(monkeypatch,tmp_path):
    store,_=fixture(monkeypatch,tmp_path)
    store.storage.data[p.PREFIX+'candidate.json']=b'wrong'
    with pytest.raises(AssertionError):p.package(store)

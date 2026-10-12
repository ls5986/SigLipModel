"""Immutable paired-candidate development package. No production promotion."""
import hashlib
import json
import logging
from pathlib import Path
import threading

CANDIDATE='f9bac37b-3ac4-4b64-9b54-cd9581bd226a'
EXPECTED_SHA='09a9cbe6877d9dfc6ee4899640c0b4dfd502d5be79a49f0d7be1cb1aa3556b6f'
DATASET_SHA='ea92b724d4dfd7f0eca65f2c534af1a4d995ee6e11ae180b85bea5e93c06dc14'
POLICY='remarks-condition-evidence-v3'
KEY='paired-dev-package:'+CANDIDATE+':'+POLICY
BUCKET='acq-training-private'
PREFIX='releases/paired-condition-dev/'+CANDIDATE+'/'+POLICY+'/'


def put_verified(store, key, data, content_type):
    digest=hashlib.sha256(data).hexdigest()
    try:
        result=store.storage.put(BUCKET,key,data,content_type=content_type)
        if result['sha256']!=digest:raise ValueError('Package upload checksum mismatch')
    except FileExistsError:
        pass  # Resume an interrupted upload only if existing bytes match.
    if store.storage.get(BUCKET,key,digest,force_network=True).read_bytes()!=data:
        raise ValueError('Package readback mismatch')
    return digest


def validate_candidate(blob):
    if hashlib.sha256(blob).hexdigest()!=EXPECTED_SHA:raise ValueError('Candidate checksum mismatch')
    b=json.loads(blob)
    if b.get('candidate_id')!=CANDIDATE or b.get('schema_version')!='actvision-paired-condition-v1':raise ValueError('Candidate identity mismatch')
    if b.get('dataset_sha256')!=DATASET_SHA:raise ValueError('Dataset identity mismatch')
    if not all(b.get('heads',{}).get(name) for name in ('vision','text','structured','fusion')):raise ValueError('Missing model component')
    if not b.get('encoders',{}).get('vision_revision') or not b['encoders'].get('text',{}).get('checkpoint_sha256'):raise ValueError('Encoder identity missing')
    return b


def package(store):
    from paired_condition_evidence import VERSION
    if VERSION!=POLICY:raise ValueError('Wrong evidence policy version')
    existing=store.document(KEY) or {}
    if existing.get('status')=='ready':
        store.storage.get(BUCKET,existing['manifest_key'],existing['manifest_sha256'])
        return existing
    root=Path(__file__).parent
    candidate=store.document('paired-condition-candidate:'+CANDIDATE)
    if not candidate:raise ValueError('Pinned candidate missing')
    blob=store.storage.get(BUCKET,candidate['artifact_key'],EXPECTED_SHA).read_bytes()
    b=validate_candidate(blob)
    files={'candidate.json':blob,
        'training_code/paired_condition_models.py':(root/'release_sources/trained_classifier.py').read_bytes(),
        'training_code/paired_condition_evidence.py':(root/'release_sources/trained_evidence.py').read_bytes(),
        'decision_policy/paired_condition_evidence.py':(root/'paired_condition_evidence.py').read_bytes(),
        'requirements-label-worker.txt':(root/'requirements-label-worker.txt').read_bytes(),
        'requirements-hosted.txt':(root/'requirements-hosted.txt').read_bytes()}
    entries={}
    for name,data in files.items():
        digest=hashlib.sha256(data).hexdigest()
        put_verified(store,PREFIX+name,data,'application/json' if name.endswith('.json') else 'text/plain')
        entries[name]={'object_key':PREFIX+name,'sha256':digest,'bytes':len(data)}
    manifest={'schema_version':'actvision-paired-dev-release-v1','release_id':CANDIDATE,
        'status':'dev_test','production_ready':False,'dataset_id':b['dataset_id'],
        'dataset_sha256':DATASET_SHA,'candidate_sha256':EXPECTED_SHA,
        'training_code_commit':b.get('code_commit'),'evidence_policy_version':POLICY,
        'encoders':b['encoders'],'artifacts':entries,'storage_bucket':BUCKET,
        'components':['vision','text','structured','fusion'],
        'strength_meaning':'uncalibrated target strength, not probability or opportunity',
        'runtime_notes':['Requires pinned encoder checkpoints; encoder weights are not duplicated in this package.',
            'Torch CPU dependency must be installed as in the model worker.',
            'Paired schema requires its own inference adapter; legacy v2 service is incompatible.',
            'Apply decision_policy after learned prediction; preserve learned results and human reviews.']}
    raw=json.dumps(manifest,sort_keys=True,separators=(',',':'),allow_nan=False).encode()
    manifest_sha=put_verified(store,PREFIX+'manifest.json',raw,'application/json')
    payload={'status':'ready','release_id':CANDIDATE,'manifest_key':PREFIX+'manifest.json',
        'manifest_sha256':manifest_sha,'candidate_sha256':EXPECTED_SHA,
        'evidence_policy_version':POLICY,'production_ready':False,'artifact_count':len(entries),
        'validation':'Candidate identity, dataset identity, component presence, encoder identities and storage readback checks passed',
        'inference_smoke_test_passed':False}
    store.save_document(KEY,payload,existing.get('revision',0))
    return payload


def start(store):
    def run():
        try:
            result=package(store)
            logging.warning('paired_dev_package status=%s release_id=%s',result['status'],CANDIDATE)
        except Exception as exc:
            logging.warning('paired_dev_package status=failed error_type=%s',type(exc).__name__)
            old=store.document(KEY) or {}
            store.save_document(KEY,{'status':'failed','error_type':type(exc).__name__,'production_ready':False},old.get('revision',0))
    threading.Thread(target=run,name='paired-dev-package',daemon=True).start()

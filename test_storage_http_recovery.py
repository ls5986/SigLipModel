import hashlib
import json
from pathlib import Path

import httpx
import pytest

from cloud_storage import PrivateStorage

PAYLOAD = b'verified immutable bytes'
SHA = hashlib.sha256(PAYLOAD).hexdigest()
BUCKET = 'acq-training-private'
PROJECT = 'a' * 20


def storage(tmp_path, handler):
    return PrivateStorage(PROJECT, 'server-secret-never-log', tmp_path,
                          client=httpx.Client(transport=httpx.MockTransport(handler)))


def test_forbidden_preserves_status_and_safe_correlation_without_retry(tmp_path, capsys):
    calls = []
    def handler(request):
        calls.append(request)
        return httpx.Response(403, text='SECRET URL TOKEN\n', headers={
            'sb-request-id': '11111111-1111-4111-8111-111111111111'})
    client = storage(tmp_path, handler)
    with pytest.raises(OSError) as caught:
        client.get(BUCKET, 'photos/test.jpg', SHA)
    assert getattr(caught.value, 'http_status', None) == 403
    assert getattr(caught.value, 'request_id', None) == '11111111-1111-4111-8111-111111111111'
    assert len(calls) == 1
    assert not list(tmp_path.iterdir())
    output = capsys.readouterr().out + str(caught.value)
    assert 'SECRET' not in output and 'server-secret' not in output


def test_service_error_is_bounded_and_success_is_verified(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time, 'sleep', lambda _: None)
    codes = iter([503, 200])
    calls = []
    def handler(request):
        calls.append(request)
        code = next(codes)
        return httpx.Response(code, content=PAYLOAD if code == 200 else b'bad gateway')
    path = storage(tmp_path, handler).get(BUCKET, 'photos/test.jpg', SHA)
    assert path.read_bytes() == PAYLOAD and len(calls) == 2


@pytest.mark.parametrize('code', [400, 401, 403, 404, 301, 302])
def test_permanent_errors_are_not_retried(tmp_path, code):
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(code, headers={'Location':'https://do-not-follow.test'}, content=b'no')
    with pytest.raises(OSError) as caught:
        storage(tmp_path, handler).get(BUCKET, 'photos/test.jpg', SHA)
    assert caught.value.http_status == code and len(calls) == 1


@pytest.mark.parametrize('code', [408, 429, 500, 502, 503, 504, 544])
def test_transient_http_failures_stop_after_three(tmp_path, monkeypatch, code):
    import time
    delays=[]
    monkeypatch.setattr(time,'sleep',delays.append)
    calls=[]
    def handler(request):
        calls.append(request)
        return httpx.Response(code,content=b'no')
    with pytest.raises(OSError):
        storage(tmp_path,handler).get(BUCKET,'photos/test.jpg',SHA)
    assert len(calls)==3 and delays==[1,2]
    assert not list(tmp_path.iterdir())


def test_long_retry_after_does_not_retry_early(tmp_path, monkeypatch):
    import time
    monkeypatch.setattr(time,'sleep',lambda _: pytest.fail('Must not sleep/retry early'))
    with pytest.raises(OSError):
        storage(tmp_path,lambda _: httpx.Response(429,headers={'Retry-After':'120'})).get(BUCKET,'p',SHA)


def test_force_network_cannot_succeed_from_cache(tmp_path):
    client=storage(tmp_path,lambda _:httpx.Response(403,text='no'))
    (tmp_path/SHA).write_bytes(PAYLOAD)
    assert client.get(BUCKET,'p',SHA).read_bytes()==PAYLOAD
    with pytest.raises(OSError): client.get(BUCKET,'p',SHA,force_network=True)
    assert (tmp_path/SHA).read_bytes()==PAYLOAD


def test_json_error_code_is_allowlisted_and_text_not_logged(tmp_path,capsys):
    client=storage(tmp_path,lambda _:httpx.Response(403,json={'code':'AccessDenied','message':'SECRET'},
        headers={'sb-request-id':'SECRET-MALFORMED'}))
    with pytest.raises(OSError) as caught:client.get(BUCKET,'p',SHA)
    assert caught.value.service_code=='AccessDenied' and caught.value.request_id is None
    assert 'SECRET' not in capsys.readouterr().out


@pytest.mark.parametrize('fault',['corrupt','oversized'])
def test_integrity_and_size_still_fail_without_retry(tmp_path,fault):
    calls=[]
    def handler(req):calls.append(req);return httpx.Response(200,content=b'wrong')
    client=storage(tmp_path,handler)
    if fault=='oversized':client.max_bytes=1
    with pytest.raises(OSError):client.get(BUCKET,'p',SHA)
    assert len(calls)==1 and not list(tmp_path.iterdir())


def test_probe_is_disabled_without_operator_uuid(monkeypatch):
    from storage_recovery import run_requested
    monkeypatch.delenv('STUDIO_STORAGE_RECOVERY_ID',raising=False)
    assert run_requested(object())=={'status':'disabled'}


@pytest.mark.parametrize('failure',['none','probe_only','http403','wrong_hash','not_in_dataset','changed_dataset','active_training','changed_during_probe','wrong_workspace'])
def test_real_storage_probe_gates_retry_and_is_single_use(tmp_path,monkeypatch,failure):
    from copy import deepcopy
    from io import BytesIO
    from uuid import uuid4
    from PIL import Image
    from storage_recovery import run_requested,REQUEST_PREFIX,TRAINING_KEY
    out=BytesIO();Image.new('RGB',(2,2)).save(out,format='JPEG')
    photo=out.getvalue();sha=hashlib.sha256(photo).hexdigest()
    rid,workspace,dataset,old=[str(uuid4()) for _ in range(4)]
    monkeypatch.setenv('STUDIO_STORAGE_RECOVERY_ID',rid)
    monkeypatch.setenv('STUDIO_WORKSPACE_ID',workspace)
    monkeypatch.setenv('STUDIO_LABEL_WORKER_WORKSPACE',workspace)
    key=REQUEST_PREFIX+rid
    request={'confirmed':True,'workspace_id':workspace,'status':'queued','operation':'probe_and_retry',
             'dataset_id':dataset,'dataset_fingerprint':'f'*64,'expected_failed_training_id':old,
             'photo_sha256s':[sha],'revision':1}
    if failure == 'probe_only': request['operation'] = 'probe'
    if failure == 'wrong_workspace': monkeypatch.setenv('STUDIO_LABEL_WORKER_WORKSPACE', str(uuid4()))
    class Store:
        def __init__(self):
            self.workspace=workspace
            self.docs={key:request,TRAINING_KEY:{'id':old,'status':'failed','revision':9,
                       'dataset_id':dataset,'dataset_fingerprint':'f'*64}}
            self.calls=0
            def handler(_):
                self.calls+=1
                if failure == 'changed_during_probe': self.docs[TRAINING_KEY]['revision'] += 1
                return httpx.Response(403,text='SECRET') if failure=='http403' else httpx.Response(200,content=b'bad' if failure=='wrong_hash' else photo)
            self.storage=storage(tmp_path,handler)
        def document(self,k):return deepcopy(self.docs.get(k))
        def save_document(self,k,payload,revision):
            assert (self.docs.get(k) or {}).get('revision',0)==revision
            self.docs[k]={**deepcopy(payload),'revision':revision+1}
            return deepcopy(self.docs[k])
    store=Store();queued=[]
    if failure == 'active_training': store.docs[TRAINING_KEY]['status'] = 'running'
    if failure == 'wrong_workspace':
        with pytest.raises(PermissionError): run_requested(store, loader=lambda *_: pytest.fail('Unauthorized load'))
        assert store.calls == 0
        return
    frozen={'dataset':{'manifest_sha256': 'x'*64 if failure=='changed_dataset' else 'f'*64},
            'rows':[{'photos':[] if failure=='not_in_dataset' else [{'sha256':sha,'storage_bucket':BUCKET,'storage_object_key':'p'}]}]}
    def enqueue(s,payload,actor):
        queued.append(payload)
        return {'request':{'id':str(uuid4()),'dataset_id':dataset,'status':'queued',
                           'dataset_fingerprint':'f'*64,'requested_by':actor}}
    result=run_requested(store,loader=lambda *_: frozen,enqueuer=enqueue)
    if failure=='none':
        assert result['status']=='retry_queued' and len(queued)==1 and store.calls==1
        assert result['checks'][0]['bytes']==len(photo)
    elif failure == 'probe_only':
        assert result['status'] == 'verified' and not queued and store.calls == 1
    else:
        assert result['status']=='failed' and not queued
    before=store.calls
    run_requested(store,loader=lambda *_:pytest.fail('Repeated load'),enqueuer=enqueue)
    assert store.calls==before
    assert 'SECRET' not in json.dumps(store.docs)


def setup_concurrent_recovery(tmp_path, monkeypatch, race):
    from copy import deepcopy
    import threading
    from types import SimpleNamespace
    from uuid import uuid4
    from PIL import Image
    from storage_recovery import REQUEST_PREFIX, TRAINING_KEY
    rid, workspace, dataset, old = [str(uuid4()) for _ in range(4)]
    for name in ('STUDIO_WORKSPACE_ID', 'STUDIO_LABEL_WORKER_WORKSPACE'):
        monkeypatch.setenv(name, workspace)
    monkeypatch.setenv('STUDIO_STORAGE_RECOVERY_ID', rid)
    key = REQUEST_PREFIX + rid
    path = tmp_path / 'photo.jpg'
    Image.new('RGB', (2, 2)).save(path, format='JPEG')
    sha = hashlib.sha256(path.read_bytes()).hexdigest()
    request = {'confirmed': True, 'workspace_id': workspace, 'status': 'queued',
               'operation': 'probe_and_retry', 'dataset_id': dataset,
               'dataset_fingerprint': 'f' * 64, 'expected_failed_training_id': old,
               'photo_sha256s': [sha], 'revision': 1}
    class Store:
        def __init__(self):
            self.workspace = workspace
            self.docs = {key: request, TRAINING_KEY: {'id': old, 'status': 'failed',
                         'revision': 3, 'dataset_id': dataset, 'dataset_fingerprint': 'f' * 64}}
            self.storage = SimpleNamespace(lock=threading.RLock(), get=lambda *a, **kw: path)
            self.enqueued = []
        def document(self, name):
            return deepcopy(self.docs.get(name))
        def save_document(self, name, value, revision):
            if (self.docs.get(name) or {}).get('revision', 0) != revision:
                raise RuntimeError('Revision conflict')
            self.docs[name] = {**deepcopy(value), 'revision': revision + 1}
            saved = deepcopy(self.docs[name])
            if race == 'claim_edit' and name == key and value['status'] == 'running':
                self.docs[key].update(revision=revision + 2, operator_note='Preserve concurrent edit')
            return saved
    store = Store()
    def enqueue(s, value, actor):
        s.enqueued.append(value)
        request_actor = 'operator:another-request' if race == 'foreign_enqueue' else actor
        return {'request': {'id': str(uuid4()), 'dataset_id': dataset, 'status': 'queued',
                            'dataset_fingerprint': 'f' * 64, 'requested_by': request_actor}}
    frozen = {'dataset': {'manifest_sha256': 'f' * 64}, 'rows': [{'photos': [{
        'sha256': sha, 'storage_bucket': BUCKET, 'storage_object_key': 'photo.jpg'}]}]}
    return store, key, lambda *_: frozen, enqueue


def test_claim_revision_never_adopts_concurrent_operator_edit(tmp_path, monkeypatch):
    from storage_recovery import run_requested
    store, key, loader, enqueue = setup_concurrent_recovery(tmp_path, monkeypatch, 'claim_edit')
    with pytest.raises(RuntimeError, match='Revision conflict'):
        run_requested(store, loader=loader, enqueuer=enqueue)
    assert store.docs[key]['operator_note'] == 'Preserve concurrent edit'
    assert store.enqueued == []


def test_concurrent_training_request_is_not_attributed_to_recovery(tmp_path, monkeypatch):
    from storage_recovery import run_requested
    store, key, loader, enqueue = setup_concurrent_recovery(tmp_path, monkeypatch, 'foreign_enqueue')
    result = run_requested(store, loader=loader, enqueuer=enqueue)
    assert result['status'] == 'failed'
    assert 'training_id' not in result


def test_own_verified_retry_is_confirmed(tmp_path, monkeypatch):
    from storage_recovery import run_requested
    store, key, loader, enqueue = setup_concurrent_recovery(tmp_path, monkeypatch, 'none')
    result = run_requested(store, loader=loader, enqueuer=enqueue)
    assert result['status'] == 'retry_queued'
    assert len(store.enqueued) == 1
    assert store.docs[key]['revision'] == 3

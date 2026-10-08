import json
import threading
from urllib.parse import urlencode
from types import SimpleNamespace

import pytest
import paired_condition
from hosted_server import create_server
from test_hosted_server import App, auth, request


def test_authenticated_training_and_correction_routes(monkeypatch):
    app = App()
    app.studio.store = object()
    calls = []
    monkeypatch.setattr(paired_condition, 'public_status', lambda store: {'request': {'status': 'none'}})
    monkeypatch.setattr(paired_condition, 'enqueue', lambda store, payload, actor: calls.append((payload, actor)) or {'status': 'queued'})
    monkeypatch.setattr(paired_condition, 'correction', lambda store, payload, actor: {'saved': True})
    server = create_server(0, app, auth(monkeypatch))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    try:
        assert request(port, 'GET', '/api/studio/paired-condition/status')[0] == 401
        form = urlencode({'username': 'owner@example.test', 'password': 'correct horse battery'})
        _, headers, _ = request(port, 'POST', '/login', form, {'Origin': 'https://studio.example.test', 'Content-Type': 'application/x-www-form-urlencoded'})
        cookie = headers['Set-Cookie'].split(';', 1)[0]
        headers = {'Cookie': cookie, 'Origin': 'https://studio.example.test', 'Content-Type': 'application/json'}
        status, _, body = request(port, 'GET', '/api/studio/paired-condition/status', headers=headers)
        assert status == 200 and json.loads(body)['token'] == 'csrf'
        payload = json.dumps({'confirmed': True})
        assert request(port, 'POST', '/api/studio/paired-condition/train', payload, headers)[0] == 403
        headers['X-Review-Token'] = 'csrf'
        monkeypatch.setenv('STUDIO_ROLE', 'reviewer')
        assert request(port, 'POST', '/api/studio/paired-condition/train', payload, headers)[0] == 403
        assert not calls
        monkeypatch.setenv('STUDIO_ROLE', 'operator')
        assert request(port, 'POST', '/api/studio/paired-condition/train', payload, headers)[0] == 200
        assert len(calls) == 1 and calls[0][1] == 'owner@example.test'
        assert request(port, 'POST', '/api/studio/paired-condition/correct', '{}', headers)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_new_worker_lane_precedes_old_batch_and_pause(monkeypatch):
    import hosted_label_worker
    monkeypatch.setenv('STUDIO_PAIRED_CONDITION_ENABLED', 'true')
    monkeypatch.setenv('STUDIO_PAIRED_AUTO_BATCH', 'old-batch')
    monkeypatch.setenv('STUDIO_WORKER_PAUSED', 'true')
    calls = []
    monkeypatch.setattr(paired_condition, 'run', lambda: calls.append(True))
    hosted_label_worker.main()
    assert calls == [True]


def test_missing_images_cannot_be_corrected_as_known(monkeypatch):
    monkeypatch.setattr(paired_condition, 'live_label', lambda store, evidence: {
        'frozen_evidence': {'photo_manifest': []}, 'evidence_id': 'example'})
    payload = {'evidence_id': 'example', 'expected_revision': 0,
        'image': {'decision': 'TARGET', 'strength': 75},
        'metadata': {'decision': 'TARGET', 'strength': 75}, 'overall': 'TARGET'}
    with pytest.raises(ValueError, match='No usable photos'):
        paired_condition.correction(object(), payload, 'reviewer')


def test_explicit_training_action_required():
    with pytest.raises(ValueError, match='Explicit training'):
        paired_condition.enqueue(object(), {}, 'operator')

from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from connected_worker import ConnectedWorker, DEV_ORIGIN, remote
from pilot import read_json


class Exchange:
    def __init__(self, root):
        self.jobs = SimpleNamespace(root=root, active=None)
        self.preview = Mock(return_value={'id': 'local'})
        self.start = Mock()
        self.cancel = Mock()
        self.result = Mock(return_value={'status': 'running'})


def worker(tmp_path, monkeypatch, call):
    import connected_worker as module
    current = {'ready': True, 'version': 'v1', 'heads_sha256': 'a'*64,
               'review_fingerprint': 'b'*64, 'reason': 'Current'}
    monkeypatch.setattr(module, 'status', lambda jobs: current)
    exchange = Exchange(tmp_path)
    value = ConnectedWorker(exchange, call=call, autostart=False)
    value.state = {'origin': DEV_ORIGIN, 'token': 'private-token', 'active': None}
    return value, exchange, current


def test_automatic_round_trip_never_exposes_token(tmp_path, monkeypatch):
    calls = []
    current = {'ready': True, 'version': 'v1', 'heads_sha256': 'a'*64, 'review_fingerprint': 'b'*64}
    def call(origin, token, path, payload=None):
        calls.append((path, payload))
        if path == 'poll':
            return {'task': {'id': 'task', 'lease': 'lease', 'request': {}, 'model': current}}
        return {'saved': True}
    value, exchange, _ = worker(tmp_path, monkeypatch, call)
    value.tick()
    exchange.start.assert_called_once_with({'id': 'local', 'confirmed': True})
    assert value.state['active']['id'] == 'task'
    exchange.result.return_value = {'status': 'completed', 'result': {'model': 'synthetic'}}
    value.tick()
    assert calls[-1] == ('complete', {'task_id': 'task', 'lease': 'lease', 'result': {'model': 'synthetic'}})
    assert value.state['active'] is None
    assert 'private-token' not in str(value.public_status())
    assert 'token' not in value.public_status()


def test_changed_reviews_report_failure_instead_of_running(tmp_path, monkeypatch):
    task = {'id': 'task', 'lease': 'lease', 'request': {}, 'model': {'heads_sha256': 'f'*64}}
    call = Mock(side_effect=[{'task': task}, {'saved': True}])
    value, exchange, _ = worker(tmp_path, monkeypatch, call)
    value.tick()
    exchange.start.assert_not_called()
    value.tick()
    assert call.call_args.args[2] == 'complete'
    assert call.call_args.args[3].get('error')


def test_cancel_and_restart_do_not_rerun(tmp_path, monkeypatch):
    call = Mock(return_value={'cancel_active': True})
    value, exchange, _ = worker(tmp_path, monkeypatch, call)
    value.state['active'] = {'id': 'task', 'lease': 'lease', 'local_id': 'local'}
    value.tick()
    exchange.cancel.assert_called_once_with('local')
    assert value.state['active'] is None
    # A crash between preview and start leaves a preview, not a running task.
    value.state['active'] = {'id': 'task', 'lease': 'lease', 'local_id': 'local'}
    exchange.result.return_value = {'status': 'preview'}
    value.tick()
    exchange.start.assert_not_called()
    assert call.call_args.args[2] == 'complete'
    assert value.state['active'] is None


def test_failed_result_upload_is_retained_for_idempotent_retry(tmp_path, monkeypatch):
    import httpx
    call = Mock(side_effect=httpx.ConnectError('synthetic network failure'))
    value, exchange, _ = worker(tmp_path, monkeypatch, call)
    value.state['active'] = {'id': 'task', 'lease': 'lease', 'local_id': 'local'}
    exchange.result.return_value = {'status': 'completed', 'result': {'synthetic': True}}
    with pytest.raises(httpx.ConnectError):
        value.tick()
    assert value.state['active']['id'] == 'task'
    exchange.start.assert_not_called()


def test_origin_is_fixed_and_credentials_saved_privately(tmp_path, monkeypatch):
    with pytest.raises(ValueError, match='dev origin'):
        remote('https://example.com', 'secret', 'claim')
    value, _, _ = worker(tmp_path, monkeypatch, Mock())
    value.save()
    assert read_json(value.file)['token'] == 'private-token'
    import os
    if os.name != 'nt':
        assert value.file.stat().st_mode & 0o777 == 0o600

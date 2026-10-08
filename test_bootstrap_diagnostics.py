import json
import pytest
from bootstrap_diagnostics import MARKER, capture_bootstrap_failure, safe_failure


class Store:
    def __init__(self, marker=None):
        self.marker = marker
        self.writes = []
    def document(self, key):
        assert key == MARKER
        return self.marker
    def save_document(self, key, payload, revision):
        assert key == MARKER
        self.writes.append((payload, revision))
        self.marker = dict(payload, revision=revision + 1)
        return self.marker


def test_safe_failure_keeps_types_and_frames_not_sensitive_messages(capsys):
    @capture_bootstrap_failure
    def start(store):
        try:
            raise ValueError('SECRET SQL and database password')
        except ValueError:
            raise OSError('SECRET storage URL') from None
    store = Store()
    result = start(store)
    assert result['status'] == 'failed'
    assert [e['error_type'] for e in result['diagnostic']['errors']] == ['OSError', 'ValueError']
    assert result['diagnostic']['errors'][0]['frames'][-1]['function'] == 'start'
    assert 'SECRET' not in json.dumps(result) + capsys.readouterr().out
    assert len(store.writes) == 1


@pytest.mark.parametrize('status', ['queued', 'completed', 'failed'])
def test_failure_cannot_overwrite_existing_terminal_or_queued_marker(status):
    @capture_bootstrap_failure
    def start(store):
        raise ValueError('bad')
    store = Store({'status': status, 'revision': 7})
    assert start(store) == store.marker
    assert store.writes == []


@pytest.mark.parametrize('status', ['disabled', 'queued', 'completed'])
def test_success_is_unchanged(status):
    @capture_bootstrap_failure
    def start(store):
        return {'status': status}
    store = Store()
    assert start(store) == {'status': status}
    assert not store.writes


def test_only_sqlstate_code_is_retained():
    class DatabaseError(Exception):
        sqlstate = '42P01'
    assert safe_failure(DatabaseError('SECRET'))['errors'][0]['sqlstate'] == '42P01'
    DatabaseError.sqlstate = 'SECRET'
    assert safe_failure(DatabaseError())['errors'][0]['sqlstate'] is None


def test_revision_guard_is_preserved():
    @capture_bootstrap_failure
    def start(store):
        raise ValueError('bad')
    store = Store({'status': 'retry_requested', 'revision': 3})
    start(store)
    assert store.writes[0][1] == 3

"""Training I/O regressions with real cache pruning and no network/model calls."""
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
import numpy as np
import pytest

from cloud_storage import PrivateStorage
from v2_training_io import artifact_preflight, embed_rows, observe_training, safe_error, stage


class Store:
    def __init__(self, storage=None):
        self.storage = storage
        self.docs = {}

    def document(self, key):
        return deepcopy(self.docs.get(key))

    def save_document(self, key, payload, revision):
        old = self.docs.get(key, {})
        assert old.get('revision', 0) == revision
        self.docs[key] = {**deepcopy(payload), 'revision': revision + 1}
        return deepcopy(self.docs[key])


def corpus(tmp_path, count=9):
    objects = {}
    photos = []
    for number in range(count):
        data = bytes([number + 1]) * 32
        sha = hashlib.sha256(data).hexdigest()
        key = 'photos/' + sha + '.jpg'
        objects['/storage/v1/object/authenticated/acq-training-private/' + key] = data
        photos.append({'sha256': sha, 'storage_bucket': 'acq-training-private',
                       'storage_object_key': key, 'room': 'kitchen'})
    calls = []
    def serve(request):
        calls.append(request.url.path)
        return httpx.Response(200, content=objects[request.url.path])
    client = httpx.Client(transport=httpx.MockTransport(serve))
    storage = PrivateStorage('a' * 20, 'server-only-fixture', tmp_path / 'cache',
                             client=client, cache_bytes=32)
    return storage, photos, objects, calls


def encoder_for(store):
    consumed = []
    batches = []
    def embed(paths):
        batches.append(len(paths))
        vectors = []
        for path in paths:
            assert path.is_file(), 'Cache pruning deleted a pending training image'
            consumed.append(path)
            data = path.read_bytes()
            vectors.append([float(data[0]), 1.0])
        return np.asarray(vectors, dtype=np.float32)
    return SimpleNamespace(_actvision_store=store, embed=embed), consumed, batches


def test_original_preload_pattern_loses_paths_before_encoding(tmp_path):
    storage, photos, _, _ = corpus(tmp_path)
    paths = [storage.get(p['storage_bucket'], p['storage_object_key'], p['sha256']) for p in photos]
    assert len(paths) == 9
    assert sum(path.is_file() for path in paths) == 1


def test_streams_all_frozen_photos_even_when_one_batch_exceeds_cache(tmp_path, monkeypatch):
    monkeypatch.setenv('STUDIO_V2_EMBED_BATCH_SIZE', '4')
    storage, photos, _, calls = corpus(tmp_path)
    rows = [{'photos': photos, 'split': 'train', 'labels': {'acquisition_fit': 'TARGET'}}]
    original = deepcopy(rows)
    siglip, consumed, batches = encoder_for(Store(storage))
    result = embed_rows(rows, siglip)
    assert set(result) == {p['sha256'] for p in photos}
    assert batches == [4, 4, 1]
    assert len(calls) == 9
    assert rows == original
    assert all(not path.exists() for path in consumed), 'Batch temporary files leaked'
    assert sum(p.stat().st_size for p in storage.cache.iterdir()) <= 32
    for photo in photos:
        number = photos.index(photo) + 1
        np.testing.assert_array_equal(result[photo['sha256']], [number, 1])


def test_duplicate_frozen_bytes_embedded_once_without_losing_room_metadata(tmp_path):
    storage, photos, _, calls = corpus(tmp_path, 1)
    rows = [{'photos': photos}, {'photos': [{**photos[0], 'room': 'bathroom'}]}]
    before = deepcopy(rows)
    siglip, _, batches = encoder_for(Store(storage))
    assert len(embed_rows(rows, siglip)) == 1
    assert batches == [1] and len(calls) == 1
    assert rows == before


def test_no_photos_never_calls_encoder_or_storage():
    def forbidden(*_):
        raise AssertionError('Must not access images')
    siglip = SimpleNamespace(_actvision_store=Store(SimpleNamespace(get=forbidden)), embed=forbidden)
    assert embed_rows([{'photos': [], 'remarks': 'Original kitchen'}], siglip) == {}


@pytest.mark.parametrize('size', ['0', '17', '-1', 'bad'])
def test_batch_size_is_bounded(tmp_path, monkeypatch, size):
    monkeypatch.setenv('STUDIO_V2_EMBED_BATCH_SIZE', size)
    storage, photos, _, calls = corpus(tmp_path, 1)
    siglip, _, _ = encoder_for(Store(storage))
    with pytest.raises(ValueError):
        embed_rows([{'photos': photos}], siglip)
    assert calls == []


def test_wrapped_transport_timeout_retries_only_three_times(tmp_path, monkeypatch):
    monkeypatch.setattr('v2_training_io.time.sleep', lambda _: None)
    storage, photos, _, _ = corpus(tmp_path, 1)
    original = storage.get
    attempts = []
    def flaky(*args):
        attempts.append(1)
        if len(attempts) < 3:
            try:
                raise httpx.ReadTimeout('SECRET http credentials')
            except httpx.HTTPError:
                raise OSError('Private Storage unavailable; retry later') from None
        return original(*args)
    storage.get = flaky
    siglip, _, _ = encoder_for(Store(storage))
    assert len(embed_rows([{'photos': photos}], siglip)) == 1
    assert len(attempts) == 3


def test_permanent_storage_error_is_not_retried_or_silently_skipped(tmp_path):
    storage, photos, _, _ = corpus(tmp_path, 1)
    attempts = []
    def denied(*_):
        attempts.append(1)
        raise OSError('Private photo download failed; no local fallback used')
    storage.get = denied
    siglip, _, _ = encoder_for(Store(storage))
    with pytest.raises(OSError):
        embed_rows([{'photos': photos}], siglip)
    assert len(attempts) == 1


def test_corrupt_bytes_are_not_accepted(tmp_path):
    storage, photos, _, _ = corpus(tmp_path, 1)
    wrong = tmp_path / 'wrong'
    wrong.write_bytes(b'incorrect')
    storage.get = lambda *_: wrong
    siglip, _, _ = encoder_for(Store(storage))
    with pytest.raises(OSError, match='integrity'):
        embed_rows([{'photos': photos}], siglip)


@pytest.mark.parametrize('result', [[], [[1, float('nan')]], [[1], [2]], [[]]])
def test_encoder_must_return_complete_finite_vectors(tmp_path, result):
    storage, photos, _, _ = corpus(tmp_path, 1)
    siglip = SimpleNamespace(_actvision_store=Store(storage), embed=lambda _: result)
    with pytest.raises(ValueError, match='invalid or incomplete'):
        embed_rows([{'photos': photos}], siglip)


def test_failure_has_durable_stage_and_safe_exception_chain(capsys):
    store = Store()
    identifier = str(uuid4())
    @observe_training
    def operation(store, request, siglip):
        stage('embedding_photos', embedded=8, total_photos=16)
        try:
            raise httpx.ReadTimeout('SECRET access token https://private.example')
        except httpx.HTTPError:
            raise OSError('SECRET sql query') from None
    with pytest.raises(OSError):
        operation(store, {'id': identifier}, None)
    saved = store.document('actvision-v2-training-progress:' + identifier)
    assert saved['stage'] == 'failed'
    assert saved['failed_stage'] == 'embedding_photos'
    assert saved['embedded'] == 8
    assert saved['diagnostic']['exception_chain'] == ['OSError', 'ReadTimeout']
    assert saved['diagnostic']['frames']
    assert 'SECRET' not in json.dumps(saved) + capsys.readouterr().out
    assert 'private.example' not in json.dumps(saved)


def test_success_progress_is_distinct_from_approval():
    store = Store()
    identifier, release = str(uuid4()), str(uuid4())
    @observe_training
    def operation(*_):
        return {'release_id': release, 'production_ready': False}
    assert operation(store, {'id': identifier}, None)['production_ready'] is False
    saved = store.document('actvision-v2-training-progress:' + identifier)
    assert saved['stage'] == 'candidate_saved' and saved['release_id'] == release


def test_preflight_upload_roundtrip_and_exact_reuse(tmp_path):
    data_by_key = {}
    gets = []
    def put(bucket, key, data, **_):
        assert bucket == 'acq-training-private'
        if key in data_by_key:
            raise FileExistsError('exists')
        data_by_key[key] = data
    def get(bucket, key, sha):
        gets.append(key)
        path = tmp_path / 'probe'
        path.write_bytes(data_by_key[key])
        return path
    store = Store(SimpleNamespace(get=get, put=put))
    request = {'id': str(uuid4())}
    artifact_preflight(store, request)
    artifact_preflight(store, request)
    assert len(data_by_key) == 1 and len(gets) == 2
    key = next(iter(data_by_key))
    data_by_key[key] = b'changed'
    with pytest.raises(OSError, match='integrity'):
        artifact_preflight(store, request)


def test_safe_error_does_not_include_message_or_paths():
    error = OSError('postgresql://user:SECRET@private/table')
    assert 'SECRET' not in json.dumps(safe_error(error))

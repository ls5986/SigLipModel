"""Explicit, single-use worker-side storage verification and optional failed-run retry.

No permissions, source evidence, labels, frozen datasets, or promotion rules change.
The operator must bind a request UUID in the worker's managed environment and queue
its matching workspace-scoped document. Importing this module performs no I/O.
"""
from datetime import datetime, timezone
import hashlib
import json
import os
import re
from uuid import UUID

from cloud_storage import StorageDownloadError

REQUEST_PREFIX = 'actvision-v2-storage-recovery:'
TRAINING_KEY = 'actvision-v2-training-current'


def _now():
    return datetime.now(timezone.utc).isoformat()


def run_requested(store, *, loader=None, enqueuer=None):
    identifier = os.environ.get('STUDIO_STORAGE_RECOVERY_ID', '').strip()
    if not identifier:
        return {'status': 'disabled'}
    identifier = str(UUID(identifier))
    key = REQUEST_PREFIX + identifier
    request = store.document(key) or {}
    if request.get('status') != 'queued':
        return request or {'status': 'not_requested'}
    if (request.get('confirmed') is not True
            or request.get('workspace_id') != str(store.workspace)
            or os.environ.get('STUDIO_LABEL_WORKER_WORKSPACE') != str(store.workspace)
            or os.environ.get('STUDIO_WORKSPACE_ID') != str(store.workspace)):
        raise PermissionError('Explicit workspace-bound recovery authorization required')
    base = {k: v for k, v in request.items() if k != 'revision'}
    # Claim once before network calls. Retain this exact write's revision, never
    # adopt a later operator edit. Crashes do not automatically replay recovery.
    claimed = store.save_document(key, {**base, 'status': 'running', 'started_at': _now()},
                                  request.get('revision', 0))
    checks = []
    try:
        if request.get('operation') not in {'probe', 'probe_and_retry'}:
            raise ValueError('Unknown storage recovery operation')
        dataset_id = str(UUID(request['dataset_id']))
        expected_id = str(UUID(request['expected_failed_training_id']))
        previous = store.document(TRAINING_KEY) or {}
        if (previous.get('id') != expected_id or previous.get('status') != 'failed'
                or previous.get('dataset_id') != dataset_id):
            raise ValueError('Failed training changed; refusing automatic recovery')
        if loader is None:
            from v2_dataset import load_frozen
            loader = load_frozen
        frozen = loader(store, dataset_id)
        fingerprint = frozen['dataset']['manifest_sha256']
        if (fingerprint != request.get('dataset_fingerprint')
                or fingerprint != previous.get('dataset_fingerprint')):
            raise ValueError('Frozen dataset identity changed')
        hashes = request.get('photo_sha256s')
        if (not isinstance(hashes, list) or not 1 <= len(hashes) <= 8
                or len(set(hashes)) != len(hashes)
                or any(not isinstance(sha, str) or not re.fullmatch(r'[a-f0-9]{64}', sha) for sha in hashes)):
            raise ValueError('Request one through eight exact frozen photo identities')
        references = {}
        # The same first-reference choice is used by embedding preparation.
        for row in frozen['rows']:
            for photo in row.get('photos') or []:
                references.setdefault(photo['sha256'], photo)
        if any(sha not in references for sha in hashes):
            raise ValueError('Recovery photo is not in the frozen dataset')
        from PIL import Image
        for sha in hashes:
            photo = references[sha]
            # Force the same authenticated network read, not a cache-only success.
            with store.storage.lock:
                path = store.storage.get(photo['storage_bucket'], photo['storage_object_key'], sha, force_network=True)
                data = path.read_bytes()
                if hashlib.sha256(data).hexdigest() != sha:
                    raise OSError('Recovery photo integrity check failed')
                with Image.open(path) as image:
                    if image.width * image.height > 40_000_000:
                        raise ValueError('Recovery image dimensions exceed limit')
                    image.load()
                    size = [image.width, image.height]
            checks.append({'photo_sha256': sha, 'status': 'verified', 'bytes': len(data), 'dimensions': size})
        result = {**base, 'status': 'verified', 'completed_at': _now(), 'checks': checks,
                  'verified_count': len(checks), 'deployed_commit': os.environ.get('RENDER_GIT_COMMIT', 'unknown')[:40]}
        if request['operation'] == 'probe_and_retry':
            recovery_now = store.document(key) or {}
            if (recovery_now.get('revision') != claimed['revision']
                    or recovery_now.get('status') != 'running'):
                raise RuntimeError('Recovery request changed; no retry queued')
            current = store.document(TRAINING_KEY) or {}
            if (current.get('id') != expected_id or current.get('status') != 'failed'
                    or current.get('revision') != previous.get('revision')):
                raise ValueError('Training state changed during probe; no retry queued')
            history_key = 'actvision-v2-training-history:' + expected_id
            if not store.document(history_key):
                store.save_document(history_key, {k: v for k, v in previous.items() if k != 'revision'}, 0)
            if enqueuer is None:
                from v2_training import enqueue
                enqueuer = enqueue
            actor = 'operator:storage-recovery:' + identifier
            queued = enqueuer(store, {'confirmed': True, 'dataset_id': dataset_id}, actor)['request']
            if (queued.get('dataset_id') != dataset_id
                    or queued.get('dataset_fingerprint') != fingerprint
                    or queued.get('requested_by') != actor
                    or queued.get('id') == expected_id
                    or queued.get('status') not in {'queued', 'running'}):
                raise RuntimeError('Training retry was not confirmed')
            result.update(status='retry_queued', training_id=queued['id'], retry_of=expected_id)
    except Exception as exc:
        diagnostic = {'error_type': type(exc).__name__}
        if isinstance(exc, StorageDownloadError):
            diagnostic.update(exc.safe_details())
        result = {**base, 'status': 'failed', 'completed_at': _now(), 'checks': checks,
                  'diagnostic': diagnostic,
                  'deployed_commit': os.environ.get('RENDER_GIT_COMMIT', 'unknown')[:40]}
    store.save_document(key, result, claimed['revision'])
    print(json.dumps({'event': 'actvision_storage_recovery', 'recovery_id': identifier,
                      'status': result['status'], 'verified_count': len(checks),
                      'training_id': result.get('training_id'),
                      'diagnostic': result.get('diagnostic')}), flush=True)
    return result

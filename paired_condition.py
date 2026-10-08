"""Frozen paired dataset adapter, bounded training/inference and simple UI API.

No external model calls, MLS refreshes, production writes or release promotion.
The independent binary schema keeps old physical-condition labels untouched.
"""
from datetime import datetime, timezone
import gc
import json
import os
from uuid import UUID, uuid4

from paired_condition_contract import POLICY, SCHEMA, KNOWN, digest

COMPONENTS = ('vision', 'text', 'structured', 'fusion')

REQUEST = 'paired-condition-training-current'
LATEST = 'paired-condition-candidate-latest'
HEARTBEAT = 'paired-condition-worker'
DATASET_NAME = 'paired-condition-automated-silver'


def now():
    return datetime.now(timezone.utc).isoformat()


def save(store, key, value):
    prior = store.document(key) or {}
    return store.save_document(key, value, prior.get('revision', 0))


def latest_dataset(store):
    with store.database.connect() as db:
        row = db.execute('''SELECT id::text id,name,version,manifest_sha256,frozen_at,label_policy
          FROM acq_training.datasets WHERE workspace_id=%s AND name=%s AND frozen_at IS NOT NULL
          ORDER BY version DESC LIMIT 1''', (store.workspace, DATASET_NAME)).fetchone()
    if not row:
        raise ValueError('No frozen paired dataset is available')
    return dict(row)


def verify_dataset(store, identifier):
    identifier = str(UUID(str(identifier)))
    with store.database.connect() as db:
        db.execute("SET LOCAL statement_timeout='60000ms'")
        d = db.execute('''SELECT id::text id,name,version,manifest_sha256,label_policy,frozen_at
          FROM acq_training.datasets WHERE workspace_id=%s AND id=%s AND name=%s AND frozen_at IS NOT NULL''',
          (store.workspace, identifier, DATASET_NAME)).fetchone()
        if not d:
            raise ValueError('Select a frozen paired condition dataset')
        verified = db.execute('''SELECT encode(extensions.digest(convert_to(jsonb_build_object(
          'policy',d.label_policy,
          'groups',(SELECT jsonb_agg(jsonb_build_object('group_id',g.group_id,'split',g.split) ORDER BY g.group_id)
            FROM acq_training.dataset_groups g WHERE g.workspace_id=d.workspace_id AND g.dataset_id=d.id),
          'items',(SELECT jsonb_agg(jsonb_build_object('example_id',i.example_id,'group_id',i.group_id,
            'photo_hashes',i.photo_hashes,'label_sha256',encode(extensions.digest(convert_to(i.label_snapshot::text,'UTF8'),'sha256'),'hex'))
            ORDER BY i.example_id) FROM acq_training.dataset_items i WHERE i.workspace_id=d.workspace_id AND i.dataset_id=d.id)
          )::text,'UTF8'),'sha256'),'hex') hash
          FROM acq_training.datasets d WHERE d.workspace_id=%s AND d.id=%s''', (store.workspace, identifier)).fetchone()
        if verified['hash'] != d['manifest_sha256']:
            raise ValueError('Frozen dataset checksum no longer matches')
        rows = db.execute('''SELECT i.example_id::text id,i.group_id::text group_id,g.split,i.label_snapshot
          FROM acq_training.dataset_items i JOIN acq_training.dataset_groups g
          ON (g.workspace_id,g.dataset_id,g.group_id)=(i.workspace_id,i.dataset_id,i.group_id)
          WHERE i.workspace_id=%s AND i.dataset_id=%s ORDER BY i.example_id''', (store.workspace, identifier)).fetchall()
    return dict(d), [dict(r) for r in rows]


class Features:
    def __init__(self, store, checkpoint):
        self.store = store
        self.checkpoint = {'backbone_revision': checkpoint['backbone_revision'],
            'text_checkpoint': {k: checkpoint['text_checkpoint'][k] for k in ('model','revision','checkpoint_sha256')}}
        self._scorer = None
        self._text = None

    def scorer(self):
        if self._scorer is None:
            from paired_auto_score import LocalScorer
            self._scorer = LocalScorer()
            if self._scorer.vision.revision != self.checkpoint['backbone_revision']:
                raise ValueError('Vision checkpoint differs from frozen dataset')
        return self._scorer

    def text(self, text):
        if self._text is None:
            from provision_semantic_encoder import verify
            from semantic_text import SemanticTextEncoder
            manifest = verify()
            if manifest['checkpoint_sha256'] != self.checkpoint['text_checkpoint']['checkpoint_sha256']:
                raise ValueError('Text checkpoint differs from frozen dataset')
            self._text = SemanticTextEncoder(manifest['directory'], manifest['checkpoint_sha256'])
        return self._text.transform([text]).toarray()[0].tolist()

    def materialize(self, label, frozen=False):
        from paired_condition_models import sanitize_text, vision_features
        evidence = label['evidence_id']
        meta = label['frozen_evidence']['metadata']
        private = label['frozen_evidence'].get('private_remarks') or ''
        public = meta.get('public_remarks') or ''
        cleaned = sanitize_text(public, private)
        manifest = label['frozen_evidence']['photo_manifest']
        input_sha = digest({'policy': 'paired-condition-inputs-v1', 'evidence': label['evidence_sha256'],
                            'text': cleaned, 'checkpoint': self.checkpoint})
        cache_key = 'paired-condition-features:' + input_sha
        cache = self.store.document(cache_key) or {}
        cached = self.store.document(label.get('feature_cache_document', '')) or {}
        if cached:
            copy = {k: v for k, v in cached.items() if k not in {'result_sha256', 'revision'}}
            if cached.get('evidence_sha256') != label['evidence_sha256'] or cached.get('result_sha256') != digest(copy):
                raise ValueError('Cached score evidence identity differs')
            if cached.get('quality_flags', {}).get('private_data_external_model_egress') is not False:
                raise ValueError('Only local caches may enter this candidate')
        photos_by_hash = {p['sha256']: p for p in cached.get('photos', [])}
        photos_by_hash.update({p['sha256']: p for p in cache.get('recovered_photos', [])})
        labels_by_hash = {p['sha256']: p for p in label.get('photo_labels', [])}
        with self.store.database.connect() as db:
            blocked = db.execute('''SELECT DISTINCT image_sha256 FROM acq_training.photos
              WHERE workspace_id=%s AND image_sha256=ANY(%s) AND (revoked_at IS NOT NULL OR retention_until<=now())''',
              (self.store.workspace, [p['sha256'] for p in manifest])).fetchall()
        blocked_hashes = {r['image_sha256'] for r in blocked}
        photos, missing = [], []
        for p in manifest:
            if p['sha256'] in blocked_hashes:
                continue
            old = photos_by_hash.get(p['sha256'])
            if not old or not old.get('image_embedding'):
                missing.append(p)
            else:
                photos.append({**old, **labels_by_hash.get(p['sha256'], {})})
        recovered = []
        for offset in range(0, len(missing), 4):
            part = missing[offset:offset + 4]
            paths = [self.store.storage.get(p['storage_bucket'], p['storage_object_key'], p['sha256']) for p in part]
            result = self.scorer().photos(paths)
            for p, result in zip(part, result):
                result = {**result, 'sha256': p['sha256']}
                recovered.append(result)
                photos.append({**result, **labels_by_hash.get(p['sha256'], {})})
        text_vector = cache.get('text_vector')
        if text_vector is None and cleaned:
            semantic = cached.get('remarks_semantic') or {}
            original = (str(public) + '\n' + str(private))[:32000]
            if (semantic.get('available') and semantic.get('checkpoint_sha256') == self.checkpoint['text_checkpoint']['checkpoint_sha256']
                    and ' '.join(original.split()) == ' '.join(cleaned.split())):
                text_vector = semantic['features']
            else:
                text_vector = self.text(cleaned)
        if not cache or recovered:
            save(self.store, cache_key, {'policy': 'paired-condition-inputs-v1', 'input_sha256': input_sha,
                'evidence_sha256': label['evidence_sha256'], 'text_vector': text_vector,
                'recovered_photos': cache.get('recovered_photos', []) + recovered, 'at': now()})
        return {'evidence_id': evidence, 'listing_key': label['listing_key'], 'photos': photos,
                'structured': meta.get('structured') or {}, 'remarks': cleaned,
                'vision_vector': vision_features(photos), 'text_vector': text_vector,
                'input_sha256': input_sha, 'evidence_sha256': label['evidence_sha256']}

    def close(self):
        self._scorer = self._text = None
        gc.collect()


def enqueue(store, payload, actor):
    if payload.get('confirmed') is not True:
        raise ValueError('Explicit training action is required')
    requested = payload.get('dataset_id') or latest_dataset(store)['id']
    requested = str(UUID(str(requested)))
    with store.database.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (store.workspace + REQUEST,))
        prior = store.database.state(db, 'document', REQUEST) or {}
        if prior.get('status') in {'queued', 'running'}:
            return prior
        # Narrow database helper copies only the existing paired dataset and
        # approved condition corrections; original snapshots stay frozen.
        dataset = db.execute('SELECT acq_training.freeze_paired_condition_next(%s,%s,%s) id',
                             (store.workspace, requested, actor)).fetchone()['id']
        row = db.execute('SELECT manifest_sha256,version FROM acq_training.datasets WHERE workspace_id=%s AND id=%s',
                         (store.workspace, dataset)).fetchone()
        runs = {name: str(uuid4()) for name in COMPONENTS}
        from psycopg.types.json import Jsonb
        for name, run in runs.items():
            db.execute('SELECT acq_training.queue_model_run_v2(%s,%s,%s,%s,%s,%s)',
                (store.workspace, run, dataset, name, 'frozen-local-encoders' if name in {'vision', 'text'} else 'none',
                 Jsonb({'orchestrator': POLICY, 'schema_version': SCHEMA, 'automated_silver': True, 'production_ready': False})))
        request = {'id': str(uuid4()), 'status': 'queued', 'dataset_id': str(dataset),
                   'dataset_sha256': row['manifest_sha256'], 'dataset_version': row['version'],
                   'run_ids': runs, 'requested_by': actor, 'requested_at': now(), 'policy': POLICY,
                   'authorization': 'Explicit user-requested development training on frozen silver labels; no production promotion'}
        return store.database.save(db, 'document', REQUEST, prior.get('revision', 0), request)


def progress(store, request, stage):
    save(store, 'paired-condition-training-progress:' + request['id'], {'stage': stage, 'at': now()})
    save(store, HEARTBEAT, {'status': 'running', 'at': now(), 'stage': stage})
    print(json.dumps({'event': 'paired_condition_training', 'stage': stage}), flush=True)


def public_status(store):
    request = store.document(REQUEST) or {'status': 'none'}
    candidate = store.document(LATEST)
    heartbeat = store.document(HEARTBEAT)
    dataset = latest_dataset(store)
    return {'request': request, 'candidate': candidate, 'worker': heartbeat,
            'progress': store.document('paired-condition-training-progress:' + request.get('id', '')),
            'dataset': {k: str(dataset[k]) if k == 'frozen_at' else dataset[k]
                        for k in ('id', 'name', 'version', 'manifest_sha256', 'frozen_at')},
            'production_ready': False}


def persist_predictions(store, candidate_id, bundle, rows):
    from paired_condition_models import predict, classify, probability
    results = predict(bundle, rows)
    for offset in range(0, len(rows), 25):
        with store.database.connect() as db:
            for row, result in zip(rows[offset:offset + 25], results[offset:offset + 25]):
                photo_head = bundle.get('photo_head')
                photo_results = []
                if photo_head:
                    import numpy as np
                    usable = [p for p in row['photos'] if p.get('exclusion') == 'none' and p.get('image_embedding')]
                    if usable:
                        scores = probability(photo_head, np.asarray([p['image_embedding'] for p in usable]))
                        photo_results = [{'sha256': p['sha256'], 'room': p.get('room'), **classify(float(score))}
                                         for p, score in zip(usable, scores)]
                key = 'paired-condition-prediction:' + candidate_id + ':' + row['evidence_id']
                if store.database.state(db, 'document', key):
                    continue
                store.database.save(db, 'document', key, 0, {**result, 'status': 'completed',
                    'candidate_id': candidate_id, 'evidence_id': row['evidence_id'], 'input_sha256': row['input_sha256'],
                    'evidence_sha256': row['evidence_sha256'], 'photos': photo_results,
                    'model_version': candidate_id, 'policy': POLICY, 'schema_version': SCHEMA, 'at': now(),
                    'evaluation_context': 'excluded_reference' if row.get('eligible') is False else row.get('effective_split', row.get('split', 'new_listing')),
                    'usable_photo_count': len([p for p in row['photos'] if p.get('exclusion') == 'none']),
                    'excluded_photos': [{'sha256': p['sha256'], 'reason': p.get('exclusion')} for p in row['photos'] if p.get('exclusion') != 'none'],
                    'production_ready': False})


def poll_training(store):
    with store.database.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (store.workspace + REQUEST,))
        request = store.database.state(db, 'document', REQUEST) or {}
        if request.get('status') != 'queued':
            return False
        for run_id in request['run_ids'].values():
            if not db.execute('SELECT acq_training.claim_model_run_v2(%s,%s) claimed', (store.workspace, run_id)).fetchone()['claimed']:
                raise ValueError('Training run was already claimed')
        request = store.database.save(db, 'document', REQUEST, request['revision'], {**request, 'status': 'running', 'started_at': now()})
    features = None
    stage = 'verify_dataset'
    try:
        progress(store, request, stage)
        dataset, items = verify_dataset(store, request['dataset_id'])
        if dataset['manifest_sha256'] != request['dataset_sha256']:
            raise ValueError('Queued dataset identity changed')
        features = Features(store, dataset['label_policy'])
        rows = []
        for index, item in enumerate(items):
            label = item['label_snapshot']
            if index % 20 == 0:
                stage = 'prepare_features_' + str(index) + '_of_' + str(len(items))
                progress(store, request, stage)
            row = features.materialize(label, frozen=True)
            rows.append({**row, 'group_id': item['group_id'], 'split': item['split'],
                         'eligible': bool(label.get('research_training_eligible')),
                         'labels': {axis: label[axis]['decision'] if isinstance(label[axis], dict) else label[axis]
                                    for axis in ('image', 'metadata', 'overall')}})
        features.close()
        features = None
        from paired_condition_models import train, guarded_splits
        bundle = train(rows, lambda s: progress(store, request, s))
        rows = guarded_splits(rows)
        candidate_id = str(uuid4())
        bundle.pop('predictions', None)
        bundle.update(candidate_id=candidate_id, created_at=now(), schema_version=SCHEMA,
                      dataset_id=dataset['id'], dataset_sha256=dataset['manifest_sha256'],
                      dataset_version=dataset['version'], encoders={
                          'vision_revision': dataset['label_policy']['backbone_revision'],
                          'text': dataset['label_policy']['text_checkpoint']},
                      code_commit=os.environ.get('RENDER_GIT_COMMIT', 'unknown'))
        stage = 'save_artifacts'
        progress(store, request, stage)
        blob = json.dumps(bundle, sort_keys=True, separators=(',', ':'), allow_nan=False).encode()
        key = 'models/paired-condition/' + candidate_id + '/candidate.json'
        stored = store.storage.put('acq-training-private', key, blob, content_type='application/json')
        sha = stored['sha256']
        from psycopg.types.json import Jsonb
        with store.database.connect() as db:
            for name, run_id in request['run_ids'].items():
                head = bundle['heads'].get(name)
                db.execute('SELECT acq_training.finish_model_run_v2(%s,%s,%s,%s,%s,%s,%s)',
                    (store.workspace, run_id, 'completed' if head else 'failed', key if head else None,
                     sha if head else None, Jsonb({'policy': POLICY, 'class_groups': head['class_groups'],
                     'evaluation': bundle['evaluation'], 'uncalibrated': True}) if head else None,
                     None if head else 'insufficient_class_support'))
            manifest = {'kind': 'release', 'schema_version': SCHEMA, 'status': 'candidate',
                'release_id': candidate_id, 'dataset_sha256': dataset['manifest_sha256'],
                'artifact_key': key, 'artifact_sha256': sha, 'components': {
                    name: {'run_id': run_id, 'supported': bundle['heads'].get(name) is not None}
                    for name, run_id in request['run_ids'].items()}, 'code_commit': bundle['code_commit'],
                'production_ready': False, 'calibrated': False, 'created_at': now(), 'label_scope': bundle['label_scope']}
            db.execute('SELECT acq_training.create_paired_condition_candidate(%s,%s,%s,%s,%s,%s,%s,%s,%s)',
                (store.workspace, candidate_id, *[request['run_ids'][n] if bundle['heads'].get(n) else None for n in COMPONENTS],
                 Jsonb({'silver_agreement': bundle['evaluation'], 'protected_slices_passed': False,
                        'human_accuracy_verified': False}), Jsonb(manifest), digest(manifest)))
        summary = {k: bundle[k] for k in ('candidate_id', 'created_at', 'dataset_id', 'dataset_sha256',
            'dataset_version', 'policy', 'schema_version', 'counts', 'training_rows', 'training_groups',
            'excluded_rows', 'alias_promotions', 'evaluation', 'code_commit', 'label_scope', 'calibrated')}
        summary.update(artifact_key=key, artifact_sha256=sha, production_ready=False,
                       components={n: bool(bundle['heads'].get(n)) for n in COMPONENTS}, photo_head=bool(bundle.get('photo_head')))
        save(store, 'paired-condition-candidate:' + candidate_id, summary)
        stage = 'save_predictions'
        progress(store, request, stage)
        persist_predictions(store, candidate_id, bundle, rows)
        save(store, LATEST, summary)
        save(store, REQUEST, {**request, 'status': 'completed', 'candidate_id': candidate_id, 'finished_at': now()})
        progress(store, request, 'completed')
        return True
    except Exception as exc:
        import traceback
        frames = [{'module': os.path.basename(f.filename), 'function': f.name, 'line': f.lineno}
                  for f in traceback.extract_tb(exc.__traceback__)[-4:]]
        save(store, REQUEST, {**request, 'status': 'failed', 'failed_at': now(), 'stage': stage,
            'error_kind': type(exc).__name__, 'frames': frames, 'reason': 'Training failed; saved data preserved; inspect this stage before retrying'})
        with store.database.connect() as db:
            for run_id in request['run_ids'].values():
                row = db.execute('SELECT status FROM acq_training.model_runs WHERE workspace_id=%s AND id=%s', (store.workspace, run_id)).fetchone()
                if row and row['status'] == 'running':
                    db.execute("SELECT acq_training.finish_model_run_v2(%s,%s,'failed',null,null,null,%s)",
                               (store.workspace, run_id, 'paired_condition_' + type(exc).__name__))
        return False
    finally:
        if features:
            features.close()


def read_bundle(store, candidate):
    path = store.storage.get('acq-training-private', candidate['artifact_key'], candidate['artifact_sha256'])
    bundle = json.loads(path.read_text())
    if bundle.get('schema_version') != SCHEMA or bundle.get('candidate_id') != candidate['candidate_id']:
        raise ValueError('Candidate artifact identity differs')
    return bundle


def live_label(store, evidence_id):
    evidence_id = str(UUID(str(evidence_id)))
    with store.database.connect() as db:
        row = db.execute('''SELECT e.*,l.listing_key,l.raw_snapshot FROM acq_training.evidence_snapshots e
          JOIN acq_training.listing_events l ON (l.workspace_id,l.id)=(e.workspace_id,e.listing_event_id)
          WHERE e.workspace_id=%s AND e.id=%s AND EXISTS(SELECT 1 FROM acq_training.property_event_roles r
            JOIN acq_training.source_rows s ON (s.workspace_id,s.id)=(r.workspace_id,r.source_row_id)
            JOIN acq_training.source_imports src ON (src.workspace_id,src.id)=(s.workspace_id,s.source_import_id)
            WHERE r.workspace_id=e.workspace_id AND r.listing_event_id=e.listing_event_id AND r.group_id=e.group_id
            AND src.schema_version='paired-workbook-v1')''', (store.workspace, evidence_id)).fetchone()
        if not row:
            raise ValueError('Listing evidence is outside the corrected workbook')
    return {'evidence_id': evidence_id, 'evidence_sha256': row['evidence_sha256'],
            'group_id': str(row['group_id']), 'listing_key': row['listing_key'],
            'frozen_evidence': {'metadata': row['metadata_snapshot'], 'photo_manifest': row['photo_manifest'],
                                'private_remarks': row['raw_snapshot'].get('PrivateRemarks')},
            'feature_cache_document': 'paired-auto-score:paired-auto-20261008-v1:' + evidence_id}


def queue_prediction(store, payload, actor):
    candidate = store.document(LATEST)
    if not candidate:
        raise ValueError('The first candidate is still training')
    label = live_label(store, payload.get('evidence_id'))
    evidence_id = label['evidence_id']
    key = 'paired-condition-prediction:' + candidate['candidate_id'] + ':' + evidence_id
    result = store.document(key)
    if result and result.get('evidence_sha256') == label['evidence_sha256']:
        return result
    request_key = 'paired-condition-inference:' + evidence_id
    prior = store.document(request_key) or {}
    if prior.get('status') in {'queued', 'running'} and prior.get('candidate_id') == candidate['candidate_id']:
        return prior
    return store.save_document(request_key, {'status': 'queued', 'candidate_id': candidate['candidate_id'],
        'evidence_id': evidence_id, 'evidence_sha256': label['evidence_sha256'], 'requested_by': actor, 'at': now()}, prior.get('revision', 0))


def poll_prediction(store):
    candidate = store.document(LATEST)
    if not candidate:
        return False
    with store.database.connect() as db:
        request_row = db.execute('''SELECT item_id FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='document' AND item_id LIKE 'paired-condition-inference:%%'
          AND payload->>'status'='queued' AND payload->>'candidate_id'=%s ORDER BY updated_at LIMIT 1''',
          (store.workspace, candidate['candidate_id'])).fetchone()
    if not request_row:
        return False
    request_key = request_row['item_id']
    request = store.document(request_key)
    features = None
    try:
        store.save_document(request_key, {**request, 'status': 'running', 'started_at': now()}, request['revision'])
        label = live_label(store, request['evidence_id'])
        if label['evidence_sha256'] != request['evidence_sha256']:
            raise ValueError('Listing evidence changed')
        bundle = read_bundle(store, candidate)
        features = Features(store, {'backbone_revision': bundle['encoders']['vision_revision'],
                                   'text_checkpoint': bundle['encoders']['text']})
        row = features.materialize(label)
        persist_predictions(store, candidate['candidate_id'], bundle, [row])
        save(store, request_key, {**request, 'status': 'completed', 'finished_at': now()})
    except Exception as exc:
        save(store, request_key, {**request, 'status': 'failed', 'error_kind': type(exc).__name__,
            'reason': 'Prediction failed; no invented result; stored listing data preserved', 'at': now()})
    finally:
        if features:
            features.close()
    return True


def correction(store, payload, actor):
    allowed = {'evidence_id', 'expected_revision', 'image', 'metadata', 'overall', 'notes', 'candidate_id', 'excluded_photos'}
    if not isinstance(payload, dict) or set(payload) - allowed:
        raise ValueError('Invalid correction fields')
    label = live_label(store, payload.get('evidence_id'))
    expected = payload.get('expected_revision')
    if type(expected) is not int or expected < 0:
        raise ValueError('Correction revision is required')
    answers = {}
    for axis in ('image', 'metadata'):
        value = payload.get(axis)
        if not isinstance(value, dict) or value.get('decision') not in KNOWN | {'INSUFFICIENT_EVIDENCE'}:
            raise ValueError('Choose Target, Not target or Cannot tell for both evidence axes')
        strength = value.get('strength')
        if value['decision'] == 'TARGET' and (type(strength) is not int or not 50 <= strength <= 100):
            raise ValueError('Target strength must be between 50 and 100')
        answers[axis] = {'decision': value['decision'], 'strength': strength if value['decision'] == 'TARGET' else None}
    if payload.get('overall') not in KNOWN | {'INSUFFICIENT_EVIDENCE'}:
        raise ValueError('Choose an overall result')
    if not isinstance(payload.get('notes', ''), str) or len(payload.get('notes', '')) > 4000:
        raise ValueError('Notes must be at most 4000 characters')
    excluded = payload.get('excluded_photos') or {}
    hashes = {p['sha256'] for p in label['frozen_evidence']['photo_manifest']}
    if (not isinstance(excluded, dict) or set(excluded) - hashes or
            any(v not in {'floor_plan', 'virtual_staging', 'shared_amenity', 'unrelated', 'unusable'} for v in excluded.values())):
        raise ValueError('Photo exclusions must reference this listing')
    if (not hashes or hashes == set(excluded)) and answers['image']['decision'] != 'INSUFFICIENT_EVIDENCE':
        raise ValueError('No usable photos: choose Cannot tell for images')
    answers.update(overall={'decision': payload['overall']}, notes={'text': payload.get('notes', '')},
                   photo_exclusions={'excluded': excluded})
    from psycopg.types.json import Jsonb
    with store.database.connect() as db:
        db.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))', (store.workspace + label['evidence_id'],))
        current = db.execute('SELECT coalesce(max(revision),0) revision FROM acq_training.property_label_events WHERE workspace_id=%s AND evidence_snapshot_id=%s',
                             (store.workspace, label['evidence_id'])).fetchone()['revision']
        if current != expected:
            raise RuntimeError('This listing changed in another window. Reload before saving.')
        for axis, value in answers.items():
            value.update(candidate_id=payload.get('candidate_id'), evidence_sha256=label['evidence_sha256'])
            db.execute('''INSERT INTO acq_training.property_label_events
              (workspace_id,group_id,evidence_snapshot_id,label_axis,label_value,answer,provenance,review_status,reviewer,revision)
              VALUES(%s,%s,%s,%s,%s,%s,'HUMAN_REVIEWED','approved',%s,%s)''',
              (store.workspace, label['group_id'], label['evidence_id'], axis, value.get('decision', 'NOTE'), Jsonb(value), actor, current + 1))
    return {'saved': True, 'revision': current + 1, 'dataset_unchanged': True}


def detail(store, group_id):
    from paired_review import PairedReview
    result = PairedReview(store).detail(group_id)
    candidate = store.document(LATEST)
    for event in result['events']:
        evidence_id = event.get('evidence_id')
        event['prediction'] = None
        if not evidence_id:
            continue
        if candidate:
            event['prediction'] = store.document('paired-condition-prediction:' + candidate['candidate_id'] + ':' + evidence_id)
        event['request'] = store.document('paired-condition-inference:' + evidence_id)
        auto = store.document('paired-auto-score:paired-auto-20261008-v1:' + evidence_id)
        event['silver_reference'] = {k: auto[k] for k in ('image', 'metadata', 'overall')} if auto else None
        event['photo_tags'] = [{k: p.get(k) for k in ('sha256','room','exclusion','decision','strength')} for p in auto.get('photos', [])] if auto else []
    result['candidate'] = candidate
    result['warning'] = ''
    return result


def run():
    import signal
    import threading
    import pilot
    torch = pilot.torch_setup()
    torch.set_num_threads(1)
    from cloud_runtime import from_env
    expected = os.environ.get('STUDIO_LABEL_WORKER_WORKSPACE')
    if not expected or expected != os.environ.get('STUDIO_WORKSPACE_ID'):
        raise ValueError('Explicit worker workspace binding required')
    store = from_env().get_studio().store
    if store.workspace != expected:
        raise ValueError('Worker workspace differs')
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    while not stop.is_set():
        save(store, HEARTBEAT, {'status': 'ready', 'at': now(), 'policy': POLICY})
        poll_training(store)
        poll_prediction(store)
        stop.wait(3)

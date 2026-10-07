"""Historical full-source enrichment. Never writes labels, datasets or releases.

Raw envelopes are workspace-scoped backend documents with immutable content IDs.
A matched listing is not a reviewed target, and a fresh historical download is
not proof that each returned field existed at the acquisition decision time.
"""
from __future__ import annotations

import base64
from collections import Counter
from copy import deepcopy
from datetime import datetime, timezone
import gzip
import hashlib
import json

from acquisition_policy import annotate_first_sales, first_sale_policy

POLICY = 'actvision-full-source-enrichment-v1'
ARCHIVE_PREFIX = 'full-source-response:'
MAX_RESPONSE_BYTES = 8 * 1024 * 1024


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                    ensure_ascii=True, default=str).encode()).hexdigest()


def _now():
    return datetime.now(timezone.utc).isoformat()


def prepare_archive(body, *, origin, received_at=None):
    if origin not in {'trestle_authenticated_response', 'user_supplied_response'}:
        raise ValueError('Explicit evidence origin required')
    if not isinstance(body, bytes) or not 0 < len(body) <= MAX_RESPONSE_BYTES:
        raise ValueError('Full response byte limit exceeded')
    received_at = received_at or _now()
    if datetime.fromisoformat(received_at.replace('Z', '+00:00')).tzinfo is None:
        raise ValueError('Receipt time must include timezone')
    payload = json.loads(body)
    rows = payload.get('value') if isinstance(payload, dict) else None
    if not isinstance(rows, list) or any(not isinstance(row, dict) for row in rows):
        raise ValueError('Expected an OData Property response')
    identities = [row.get('ListingKey') for row in rows]
    if any(not isinstance(key, str) or not key or len(key) > 512 for key in identities) or len(set(identities)) != len(identities):
        raise ValueError('Response keys must be unique exact ListingKeys')
    sha = hashlib.sha256(body).hexdigest()
    records = [{'listing_key': row['ListingKey'], 'listing_id': row.get('ListingId'),
                'source_modified_at': row.get('ModificationTimestamp'),
                'field_count': len(row), 'record_sha256': digest(row), 'response_index': i}
               for i, row in enumerate(rows)]
    return {'policy': POLICY, 'source': origin, 'receipt_at': received_at,
            'response_sha256': sha, 'body_bytes': len(body), 'resource': 'Property',
            'body_gzip_base64': base64.b64encode(gzip.compress(body, mtime=0)).decode(),
            'records': records, 'related_resources': 'only_what_is_in_response',
            'raw_visibility': 'workspace_backend_only',
            'historical_field_eligibility': 'not_established_by_receipt'}


def archive_response(store, body, *, origin, received_at=None):
    prepared = prepare_archive(body, origin=origin, received_at=received_at)
    key = ARCHIVE_PREFIX + prepared['response_sha256']
    existing = store.document(key)
    if existing is None:
        store.save_document(key, prepared, 0)
    elif (existing.get('response_sha256') != prepared['response_sha256']
          or existing.get('body_gzip_base64') != prepared['body_gzip_base64']):
        raise ValueError('Content-addressed source archive conflict')
    return {'archive_id': key, 'sha256': prepared['response_sha256'],
            'bytes': prepared['body_bytes'], 'records': prepared['records']}


def _selection(row, validation):
    snap = row.get('source_snapshot') or {}
    event = snap.get('event_map') or {}
    decision = validation.get('decision')
    if decision in {'wrong_listing', 'wrong_era', 'unsure'}:
        return None, 'current_validation_' + decision
    if event.get('recovery_status') == 'acquisition_mls_unavailable':
        return None, 'acquisition_listing_unavailable'
    explicit = event.get('acquisition_listing_key')
    reviewed = validation.get('selected_listing_key') if decision == 'confirmed' and validation.get('certified_for_training') is True else None
    if explicit and reviewed and explicit != reviewed:
        return None, 'conflicting_confirmed_listing'
    key = explicit or reviewed or row.get('listing_key')
    confirmed = bool(reviewed or row.get('match_status') == 'confirmed')
    if not confirmed or not key:
        return None, 'listing_not_confirmed'
    candidates = [c for c in snap.get('mls_candidates', [])
                  if str((c.get('listing') or {}).get('ListingKey') or '') == key]
    if not candidates:
        return None, 'selected_listing_evidence_missing'
    if any(c != candidates[0] for c in candidates[1:]):
        return None, 'conflicting_listing_snapshots'
    candidate = deepcopy(candidates[0])
    if event.get('recovery_status') == 'mapped':
        candidate['match'] = {**candidate.get('match', {}), 'identity_chronology_override': {
            'prior_sale_date': event.get('prior_sale_date'), 'after_close_date': event.get('after_close_date')}}
    if row.get('first_sale_date'):
        candidate['match'] = {**candidate.get('match', {}), 'first_actual_sale_date_2026': row['first_sale_date']}
    policy = first_sale_policy(candidate, snap.get('spreadsheet') or {}, snap.get('mls_candidates') or [])
    if not policy['supported'] or not policy.get('selected_close_date'):
        return None, 'acquisition_time_not_supported'
    return {'candidate': candidate, 'sale_policy': policy}, None


def build_manifest(examples, validations, era_reviews=None):
    """Deduplicate exact confirmed acquisition events; never auto-select best match."""
    rows = deepcopy(list(examples))
    annotate_first_sales(rows)
    items, excluded = {}, []
    era_reviews = era_reviews or {}
    for row in rows:
        eid = str(row['id'])
        validation = validations.get(eid) or {}
        selected, reason = _selection(row, validation)
        if reason:
            excluded.append({'example_id': eid, 'reason': reason})
            continue
        listing = selected['candidate']['listing']
        key = listing['ListingKey']
        if (era_reviews.get(key) or {}).get('decision') == 'wrong_era':
            excluded.append({'example_id': eid, 'reason': 'current_photo_era_rejected'})
            continue
        item = items.setdefault(key, {'listing_key': key, 'listing_id': listing.get('ListingId'),
            'group_id': str(row['group_id']), 'expected_close_date': selected['sale_policy']['selected_close_date'],
            'expected_parcel_number': listing.get('ParcelNumber'), 'expected_street_number': listing.get('StreetNumber'),
            'bindings': [], 'source': 'confirmed_acquisition_event',
            'field_time_eligibility': 'not_established_by_backfill'})
        item['bindings'].append({'example_id': eid, 'group_id': str(row['group_id']),
            'validation_revision': validation.get('revision', 0),
            'binding_sha256': digest({'example': row, 'validation': validation,
                                    'era': era_reviews.get(key)})})
        if item['group_id'] != str(row['group_id']): item['_conflict'] = True
        if item['expected_close_date'] != selected['sale_policy']['selected_close_date']: item['_conflict'] = True
    for key in list(items):
        if items[key].pop('_conflict', False):
            excluded.extend({'example_id': b['example_id'], 'reason': 'conflicting_physical_groups'}
                            for b in items[key]['bindings'])
            del items[key]
    values = [items[key] for key in sorted(items)]
    return {'policy': POLICY, 'status': 'planned', 'items': values, 'excluded': excluded,
            'counts': {'source_rows': len(rows), 'listing_events': len(values),
                       'physical_groups': len({v['group_id'] for v in values}),
                       'excluded_rows': len(excluded), 'excluded_reasons': dict(Counter(x['reason'] for x in excluded))},
            'fingerprint': digest(values), 'training_or_promotion_requested': False}


def review_status(examples, validations, review):
    plan = build_manifest(examples, validations)
    confirmed = bool(plan['items']) and not plan['excluded']
    values = sorted({str(r.get('reference_label')) for r in examples if r.get('reference_label')})
    target = review.get('acquisition_fit') or {'target': 'TARGET', 'not_target': 'NOT_TARGET', 'unsure': 'UNKNOWN'}.get(review.get('target_fit'))
    reviewed = review.get('status') == 'approved' and target in {'TARGET','NOT_TARGET','UNKNOWN'}
    return {'source_match': 'confirmed' if confirmed else 'unconfirmed',
            'imported_reference': values[0] if len(values) == 1 else values,
            'imported_reference_notice': 'Imported cohort suggestion, not your reviewed target decision',
            'target_review': 'reviewed' if reviewed else 'not_reviewed',
            'reviewed_target': target if reviewed else None,
            'training_eligible': None if confirmed else False,
            'training_notice': 'Eligibility requires current evidence, axis reviews and dataset validation; backfill does not approve labels.',
            'source_blockers': sorted({r['reason'] for r in plan['excluded']})}


def load_manifest(store):
    with store.database.connect() as db:
        rows = db.execute('''SELECT id,group_id,listing_key,listing_id,match_status,
            reference_label,reference_is_gold,source_snapshot
            FROM acq_training.examples WHERE workspace_id=%s ORDER BY id''', (store.workspace,)).fetchall()
        state = db.execute('''SELECT kind,item_id,payload,revision FROM acq_training.studio_state
            WHERE workspace_id=%s AND (kind='era' OR (kind='document' AND item_id LIKE 'mls-validation:%%'))''',
            (store.workspace,)).fetchall()
    validations = {r['item_id'].removeprefix('mls-validation:'): {**r['payload'], 'revision': r['revision']}
                   for r in state if r['kind'] == 'document'}
    era = {r['item_id']: r['payload'] for r in state if r['kind'] == 'era'}
    return build_manifest(rows, validations, era)

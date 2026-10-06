"""Regression coverage for real first-candidate startup failures, not gate bypasses."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace
from uuid import uuid4

import pytest

from cloud_store import SupabaseStore
from validated_listing_store import (
    ACCEPTED_SQL, ValidatedListingStore, accepted_media, accepted_projection,
)
from v2_frozen_items import FORMAT, pack_rows, row_order, unpack_rows


def accepted_row():
    return {
        'id': str(uuid4()), 'group_id': str(uuid4()), 'listing_key': None,
        'source_rows': [1], 'protected_test': True,
        'source_snapshot': {
            'spreadsheet': {'Prior Sale Date': '2020-01-01'},
            'mls_candidates': [{
                'listing': {'ListingKey': 'accepted-mls', 'CloseDate': '2020-01-01',
                            'PublicRemarks': 'Original kitchen.', 'YearBuilt': 1960},
                'match': {'exact_apn': True, 'street_number_matches': True,
                          'unit_conflict': False},
            }],
        },
        'accepted_validation': {'decision': 'confirmed', 'certified_for_training': True,
                                'selected_listing_key': 'accepted-mls'},
    }


def media_record():
    return {'listing_key': 'accepted-mls', 'status': 'sampled', 'images': [{
        'provider_media_key': 'photo1', 'image_sha256': 'a' * 64,
        'storage_bucket': 'private-test', 'storage_object_key': 'test/photo.jpg',
        'context': 'unknown',
        'context_evidence': {'validation_listing_key': 'accepted-mls'},
    }]}


def test_accepted_projection_preserves_source_and_exact_selected_listing():
    row = accepted_row()
    before = deepcopy(row)
    projected = accepted_projection(row, 'accepted-mls')
    assert row == before
    assert projected['listing_key'] == 'accepted-mls'
    assert projected['id'] == row['id']
    assert projected['protected_test'] is True
    assert SupabaseStore._selected(projected)['listing']['ListingKey'] == 'accepted-mls'


@pytest.mark.parametrize('mutation', [
    lambda r: r['accepted_validation'].update(decision='wrong_listing'),
    lambda r: r['accepted_validation'].update(certified_for_training=False),
    lambda r: r['accepted_validation'].update(selected_listing_key='other'),
    lambda r: r.update(listing_key='existing'),
    lambda r: r['source_snapshot'].update(event_map={'acquisition_listing_key': 'newer'}),
    lambda r: r['source_snapshot'].update(event_map={'recovery_status': 'acquisition_mls_unavailable'}),
    lambda r: r['source_snapshot'].update(mls_candidates=[]),
])
def test_lookup_does_not_invent_or_reaccept_source_evidence(mutation):
    row = accepted_row()
    mutation(row)
    assert accepted_projection(row, 'accepted-mls') is None


def test_conflicting_saved_candidates_are_not_auto_resolved():
    row = accepted_row()
    other = deepcopy(row['source_snapshot']['mls_candidates'][0])
    other['listing']['PublicRemarks'] = 'Different event'
    row['source_snapshot']['mls_candidates'].append(other)
    with pytest.raises(ValueError, match='conflicting'):
        accepted_projection(row, 'accepted-mls')


def test_accepted_sql_is_workspace_scoped_and_requires_current_confirmation():
    assert 'e.workspace_id=%s' in ACCEPTED_SQL
    assert "v.payload->>'decision'='confirmed'" in ACCEPTED_SQL
    assert "v.payload->>'certified_for_training'='true'" in ACCEPTED_SQL
    assert 'v.workspace_id=e.workspace_id' in ACCEPTED_SQL


def test_unknown_property_falls_back_to_current_validation(monkeypatch):
    row = accepted_row()
    class DB:
        workspace = str(uuid4())
        def execute(self, sql, params):
            assert sql == ACCEPTED_SQL
            assert params == (self.workspace, 'accepted-mls')
            return SimpleNamespace(fetchall=lambda: [row])
    db = DB()
    store = ValidatedListingStore(db, None)
    def missing(*args):
        raise ValueError('Unknown property')
    monkeypatch.setattr(SupabaseStore, '_examples', missing)
    assert store._examples(db, 'accepted-mls')[0]['listing_key'] == 'accepted-mls'


@pytest.mark.parametrize('error', [OSError('database unavailable'), ValueError('conflicting photos')])
def test_lookup_never_swallows_other_failures(monkeypatch, error):
    store = ValidatedListingStore(SimpleNamespace(workspace='test'), None)
    def failing(*args):
        raise error
    monkeypatch.setattr(SupabaseStore, '_examples', failing)
    with pytest.raises(type(error), match=str(error)):
        store._examples(None, 'accepted-mls')


def test_accepted_media_retains_protected_group_and_content_hash():
    row, media = accepted_row(), media_record()
    photos = accepted_media(row, media, 'accepted-mls')
    assert len(photos) == 1
    assert photos[0]['image_sha256'] == 'a' * 64
    assert photos[0]['image_id'] == 'validation:' + row['id'] + ':photo1'
    assert photos[0]['protected_test'] is True


@pytest.mark.parametrize('mutation', [
    lambda m: m.update(listing_key='other'),
    lambda m: m.update(status='failed'),
    lambda m: m['images'][0]['context_evidence'].update(event_role='after'),
    lambda m: m['images'][0]['context_evidence'].update(validation_listing_key='other'),
    lambda m: m['images'][0].update(revoked_at='2020-01-01T00:00:00Z'),
    lambda m: m['images'][0].update(retention_until='2020-01-01T00:00:00Z'),
    lambda m: m['images'][0].update(image_sha256='invalid'),
])
def test_wrong_era_or_unretained_media_stays_excluded(mutation):
    media = media_record()
    mutation(media)
    assert accepted_media(accepted_row(), media, 'accepted-mls') == []


def event_rows(n=1):
    rows = []
    for i in range(n):
        example, group = str(uuid4()), str(uuid4())
        for role, label in [('acquisition', 'TARGET'), ('after', 'NOT_TARGET')]:
            rows.append({
                'property_id': role + '-' + str(i), 'event_role': role,
                'example_id': example, 'source_group_id': group, 'split_group_id': group,
                'split': 'train', 'evidence_id': 'b' * 64,
                'labels': {'physical_condition': 'UNKNOWN', 'modernization': 'UNKNOWN',
                           'acquisition_fit': label, 'text_signals': {}},
                'provenance': {'acquisition_fit': {'origin': 'IMPORTED_TARGET' if role == 'acquisition'
                    else 'POST_RENOVATION_OUTCOME', 'source_id': 'test:' + example}},
                'remarks': 'Original' if role == 'acquisition' else 'Renovated',
                'structured': {'YearBuilt': 1960},
                'photos': [{'photo_id': role + ':photo', 'sha256': ('a' if role == 'acquisition' else 'c') * 64,
                            'storage_bucket': 'private-test', 'storage_object_key': role + '.jpg', 'room': 'kitchen'}],
                'available_modalities': ['vision', 'text', 'structured'],
            })
    return sorted(rows, key=row_order)


def records_for(groups, items):
    splits = {row['group_id']: row['split'] for row in groups}
    return [{**deepcopy(item), 'split': splits[item['group_id']]} for item in items]


def test_two_event_rows_fit_one_existing_example_primary_key():
    rows = event_rows()
    groups, items = pack_rows(rows)
    assert len(items) == 1
    assert len(items[0]['label_snapshot']['events']) == 2
    restored = unpack_rows(records_for(groups, items), FORMAT)
    assert restored == rows
    assert {row['labels']['acquisition_fit'] for row in restored} == {'TARGET', 'NOT_TARGET'}


@pytest.mark.parametrize('field', ['source_group_id', 'split_group_id'])
def test_materialization_rejects_split_leakage(field):
    rows = event_rows()
    rows[1]['split'] = 'test'
    with pytest.raises(ValueError, match='leaked'):
        pack_rows(rows)


def test_duplicate_canonical_event_is_rejected():
    rows = event_rows()
    with pytest.raises(ValueError, match='Duplicate'):
        pack_rows(rows + [deepcopy(rows[0])])


def test_tampered_photo_manifest_is_rejected():
    groups, items = pack_rows(event_rows())
    records = records_for(groups, items)
    records[0]['photo_hashes'] = []
    with pytest.raises(ValueError, match='photo manifest'):
        unpack_rows(records, FORMAT)


def test_snapshot_cannot_override_relational_identity():
    groups, items = pack_rows(event_rows())
    records = records_for(groups, items)
    records[0]['label_snapshot']['events'][0]['split'] = 'test'
    with pytest.raises(ValueError, match='identity'):
        unpack_rows(records, FORMAT)


def test_frozen_dataset_full_roundtrip_and_tamper_detection(monkeypatch):
    import v2_dataset as dataset
    from actvision_contract import digest
    rows = event_rows(20)
    manifest = {'rows': rows, 'counts': {'properties': 40},
                'fingerprint': digest({'policy': dataset.LABEL_POLICY_VERSION,
                    'split_policy': dataset.SPLIT_POLICY_VERSION, 'rows': rows})}
    class DB:
        workspace = str(uuid4())
        @contextmanager
        def connect(self):
            yield self
        def execute(self, sql, params):
            if 'max(version)' in sql:
                return SimpleNamespace(fetchone=lambda: {'version': 1})
            if 'create_frozen_dataset_v2' in sql:
                _, identifier, _, version, fingerprint, policy, groups, items = params
                self.dataset = {'id': identifier, 'version': version, 'manifest_sha256': fingerprint,
                                'label_policy': deepcopy(policy.obj), 'frozen_at': 'fixture'}
                self.records = records_for(groups.obj, items.obj)
                assert len(self.records) == 20
                assert len({r['example_id'] for r in self.records}) == 20
                return SimpleNamespace()
            if 'FROM acq_training.datasets' in sql:
                return SimpleNamespace(fetchone=lambda: self.dataset)
            if 'FROM acq_training.dataset_items' in sql:
                return SimpleNamespace(fetchall=lambda: self.records)
            raise AssertionError('Unexpected query: ' + sql)
    class Store:
        def __init__(self):
            self.database = DB()
            self.workspace = self.database.workspace
            self.docs = {}
        def document(self, key):
            return self.docs.get(key)
        def save_document(self, key, payload, revision):
            self.docs[key] = deepcopy(payload)
    store = Store()
    monkeypatch.setattr(dataset, 'build', lambda _: deepcopy(manifest))
    frozen = dataset.freeze(store, {'confirmed': True})
    loaded = dataset.load_frozen(store, frozen['id'])
    assert loaded['rows'] == rows
    assert loaded['dataset']['manifest_sha256'] == manifest['fingerprint']
    store.database.records[0]['label_snapshot']['events'][0]['labels']['acquisition_fit'] = 'UNKNOWN'
    with pytest.raises(ValueError, match='fingerprint'):
        dataset.load_frozen(store, frozen['id'])


def test_after_event_uses_the_same_validation_split():
    import v2_dataset as dataset
    group = next(str(i) for i in range(1000) if dataset._split(str(i), False) == 'validation')
    example = str(uuid4())
    record = {'id': example, 'group_id': group, 'photo_uuid': None,
              'candidate': {'listing': {'ListingKey': 'after', 'PublicRemarks': 'Renovated.', 'YearBuilt': 1960}}}
    class DB:
        @contextmanager
        def connect(self):
            yield self
        def execute(self, *args):
            return SimpleNamespace(fetchall=lambda: [record])
    store = SimpleNamespace(workspace='test', database=DB())
    after = dataset._after_event_examples(store, [{'example_id': example, 'group_id': group, 'split': 'train'}])
    assert len(after) == 1
    assert after[0]['split'] == 'validation'


def test_structured_bad_container_value_is_not_a_startup_typeerror():
    from v2_dataset import _structured
    assert _structured({'YearBuilt': [], 'PropertyType': {}, 'BedroomsTotal': 3}) == {'BedroomsTotal': 3}

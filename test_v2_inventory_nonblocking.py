"""Overview counters must remain independent of long-running training previews."""
from contextlib import contextmanager
from copy import deepcopy
from types import SimpleNamespace

import pytest

import v2_inventory


class Store:
    workspace = 'fixture-workspace'

    def __init__(self, latest=None):
        self.latest = latest
        self.documents = []
        self.queries = []
        self.database = self
        self.answers = iter([
            {'target_properties': 3, 'acquisition_mls_listings': 2,
             'source_only_acquisitions': 1, 'unresolved_event_maps': 0},
            {'count': 1},
            {'ready': 1, 'missing': 1},
            {'count': 0},
        ])

    @contextmanager
    def connect(self):
        yield self

    def execute(self, sql, params):
        self.queries.append(sql)
        assert all(param == self.workspace for param in params)
        return SimpleNamespace(fetchone=lambda: next(self.answers))

    def queue(self, args):
        return {
            'items': [{'id': 'one', 'image_count': 2, 'status': 'reviewed'},
                      {'id': 'two', 'image_count': 0, 'status': 'unreviewed'}],
            'total': 2, 'inventory': {'imported_rows': 4, 'physical_groups': 3,
                                     'matched_listings': 2},
        }

    def property(self, identifier):
        raise AssertionError('Inventory must not rebuild property-level training evidence')

    def document(self, key):
        self.documents.append(key)
        assert key == 'actvision-v2-dataset-latest'
        return deepcopy(self.latest)

    def save_document(self, *args):
        raise AssertionError('Inventory is read-only')


def test_inventory_does_not_invoke_training_preview(monkeypatch):
    import v2_dataset
    def forbidden(*args, **kwargs):
        pytest.fail('Inventory must not invoke full dataset assembly')
    monkeypatch.setattr(v2_dataset, 'preview', forbidden)
    monkeypatch.setattr(v2_dataset, 'build', forbidden)
    store = Store()
    result = v2_inventory.status(store)
    assert result['counts']['target_properties'] == 3
    assert result['counts']['acquisition_mls_listings'] == 2
    assert result['counts']['current_acquisition_ai_drafts'] == 1
    assert result['counts']['missing_acquisition_ai_drafts'] == 1
    assert result['counts']['human_reviewed'] == 1
    assert result['counts']['training_eligible'] is None
    assert result['training']['current_evidence_evaluated'] is False
    assert result['training']['frozen_dataset'] is None
    assert result['training_status_error'] is None
    assert len(store.queries) == 4
    assert store.documents == ['actvision-v2-dataset-latest']


def test_saved_frozen_counts_are_explicitly_historical_not_live_eligibility():
    latest = {'id': 'dataset-id', 'version': 3, 'fingerprint': 'a' * 64,
              'frozen_at': '2026-01-01T00:00:00Z',
              'counts': {'properties': 200, 'splits': {'train': 150, 'test': 50}}}
    result = v2_inventory.status(Store(latest))
    assert result['counts']['training_eligible'] is None
    assert result['counts']['protected_test'] is None
    training = result['training']
    assert training['available'] is False
    assert training['basis'] == 'explicit_preview_required'
    assert training['frozen_dataset']['basis'] == 'last_frozen_dataset'
    assert training['frozen_dataset']['counts']['properties'] == 200
    assert training['frozen_dataset']['fingerprint'] == latest['fingerprint']
    assert latest['counts']['properties'] == 200


def test_status_does_not_disguise_storage_failure_as_zero_counts():
    store = Store()
    def unavailable(key):
        raise OSError('Storage unavailable')
    store.document = unavailable
    with pytest.raises(OSError, match='Storage unavailable'):
        v2_inventory.status(store)

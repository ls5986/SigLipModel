from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

import joblib
import pytest
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.pipeline import Pipeline

import pilot
from acq_exchange import Exchange, digest, validate_request
from comparison_worker import process, public_addresses
from model_loop import fingerprint, status
from property_models import metadata_features
from studio_jobs import StudioJobs
from studio_worker import training_bundle
from test_studio_data import imported_store


def request():
    value = {"schema_version": "acq-siglip-request-v1", "comparison_id": str(uuid4()),
             "baseline": {"condition_bucket": "DATED_VALUE_ADD"},
             "photos": [{"image_id": "photo-1", "url": "https://example.com/photo.jpg"}]}
    return {**value, "request_sha256": digest(value)}


def property_request():
    value = {
        "schema_version": "acq-property-request-v2",
        "comparison_id": str(uuid4()),
        "property_id": "listing-1",
        "baseline": {},
        "metadata": {"YearBuilt": 1965, "PropertySubType": "Single Family Residence"},
        "photos": [],
        "requested_mode": "automatic",
    }
    return {**value, "request_sha256": digest(value)}


def trained(tmp_path):
    store = imported_store(tmp_path)
    jobs = StudioJobs(store, store.root)
    rows, _ = jobs._snapshot()
    properties = jobs._property_snapshot()
    folder = jobs.folder / ('a' * 32)
    folder.mkdir()
    file = folder / 'studio_heads.joblib'
    metadata = Pipeline([
        ("vectorize", DictVectorizer()),
        ("classifier", LogisticRegression()),
    ]).fit([
        metadata_features({"YearBuilt": 2020, "PropertySubType": "Condo"}),
        metadata_features({"YearBuilt": 2018, "PropertySubType": "Condo"}),
        metadata_features({"YearBuilt": 1960, "PropertySubType": "Single Family"}),
        metadata_features({"YearBuilt": 1970, "PropertySubType": "Single Family"}),
    ], [0, 0, 1, 1])
    joblib.dump({
        "backbone_revision": 'b' * 40,
        "property_models": {"metadata": metadata},
    }, file)
    pilot.write_json(folder / 'snapshot.json', {"examples": rows, "properties": properties})
    pilot.write_json(store.root / 'artifacts' / 'backbone.json', {"revision": 'b' * 40})
    pilot.write_json(store.root / 'artifacts' / 'studio_candidate_latest.json', {
        "folder": str(folder), "version": folder.name, "heads_sha256": pilot.sha(file),
        "review_fingerprint": fingerprint(rows, properties),
    })
    return store, jobs


def test_real_review_changes_invalidate_candidate_and_preview(tmp_path):
    store, jobs = trained(tmp_path)
    assert status(jobs)['ready']
    exchange = Exchange(jobs)
    preview = exchange.preview(request())
    store.save_review({"kind": "image", "id": "p1:m1", "expected_revision": 0,
                       "reviewer": "Human", "status": "approved", "room": "kitchen",
                       "features": {}, "preference": "target"})
    assert not status(jobs)['ready']
    with pytest.raises(ValueError, match='changed'):
        exchange.start({"id": preview['id'], "confirmed": True})
    assert exchange.active is None


def test_supported_metadata_change_invalidates_candidate(tmp_path):
    store, jobs = trained(tmp_path)
    assert status(jobs)["ready"]
    with store.connect() as db:
        row = db.execute("SELECT metadata FROM properties WHERE id='p1'").fetchone()
        import json
        metadata = json.loads(row["metadata"])
        metadata["YearBuilt"] = 1975
        db.execute("UPDATE properties SET metadata=? WHERE id='p1'", (json.dumps(metadata),))
    assert not status(jobs)["ready"]


def test_drafts_are_not_training_preferences(tmp_path):
    store = imported_store(tmp_path)
    store.save_review({"kind": "image", "id": "p1:m1", "expected_revision": 0,
                       "reviewer": "Human", "status": "draft", "room": "kitchen",
                       "features": {}, "preference": "target"})
    rows, _ = StudioJobs(store, store.root)._snapshot()
    assert rows[0]['preference'] is None


def test_training_cannot_retain_old_preference_heads(tmp_path, monkeypatch):
    monkeypatch.setattr(pilot, 'ARTIFACTS', tmp_path)
    joblib.dump({'preference_models': {'kitchen': 'obsolete'}, 'room_model': 'baseline'}, tmp_path / 'silver_heads.joblib')
    bundle, _ = training_bundle()
    assert bundle['preference_models'] == {}
    assert bundle['room_model'] == 'baseline'


def test_request_and_network_guards():
    value = request()
    assert validate_request(value) == ['example.com']
    value['photos'][0]['url'] = 'http://localhost/secret'
    value['request_sha256'] = digest({k: v for k, v in value.items() if k != 'request_sha256'})
    with pytest.raises(ValueError, match='HTTPS'):
        validate_request(value)
    with patch('socket.getaddrinfo', return_value=[(None, None, None, None, ('127.0.0.1', 443))]):
        with pytest.raises(ValueError, match='public'):
            public_addresses('example.com')


def test_v2_request_supports_metadata_only_and_rejects_outcome_fields():
    value = property_request()
    assert validate_request(value) == []
    value["metadata"]["ClosePrice"] = 500000
    value["request_sha256"] = digest({k: v for k, v in value.items() if k != "request_sha256"})
    with pytest.raises(ValueError, match="unsupported"):
        validate_request(value)


def test_v2_metadata_only_process_routes_without_downloading_photos(tmp_path, monkeypatch):
    store, jobs = trained(tmp_path)
    exchange = Exchange(jobs)
    preview = exchange.preview(property_request())
    monkeypatch.setattr(pilot, "ARTIFACTS", store.root / "artifacts")
    result = process(
        exchange.folder / preview["id"],
        downloader=lambda *_: pytest.fail("Metadata-only inference must not download"),
        predictor=lambda paths, bundle, backbone: [],
    )
    assert result["schema_version"] == "acq-property-result-v2"
    assert result["prediction"]["mode_used"] == "metadata_only"
    assert result["prediction"]["component_scores"]["metadata"] is not None
    assert result["prediction"]["decision"] == "NEEDS_REVIEW"


def test_bounded_exchange_preserves_training_and_outputs_model_identity(tmp_path, monkeypatch):
    store, jobs = trained(tmp_path)
    exchange = Exchange(jobs)
    preview = exchange.preview(request())
    monkeypatch.setattr(pilot, 'ARTIFACTS', store.root / 'artifacts')
    folder = exchange.folder / preview['id']
    result = process(folder, downloader=lambda url, path: path.write_bytes(b'synthetic-image'),
                     predictor=lambda paths, bundle, backbone: [{'room': 'kitchen', 'preference_score': .7}])
    assert result['model']['reviews_current'] is True
    assert result['model']['heads_sha256'] == status(jobs)['heads_sha256']
    assert result['photos'][0]['training_overlap'] == 'unknown'
    assert not list(folder.glob('*.image'))
    assert store.property('p1')['images'][0]['review']['status'] == 'unreviewed'
    assert jobs.list_jobs() == []


def test_failed_download_removes_partial_images(tmp_path, monkeypatch):
    store, jobs = trained(tmp_path)
    exchange = Exchange(jobs)
    preview = exchange.preview(request())
    monkeypatch.setattr(pilot, 'ARTIFACTS', store.root / 'artifacts')
    folder = exchange.folder / preview['id']
    def fail(url, path):
        path.write_bytes(b'partial')
        raise ValueError('download failed')
    with pytest.raises(ValueError, match='download failed'):
        process(folder, downloader=fail)
    assert not list(folder.glob('*.image'))
    assert not (folder / 'result.json').exists()

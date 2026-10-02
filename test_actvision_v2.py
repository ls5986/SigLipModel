import copy
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import threading

import joblib
import numpy as np
import pytest

from actvision_contract import (
    SCHEMA, digest, remarks_digest, unavailable_component, unknown_result, validate_contract,
)
from condition_schema import PHYSICAL_CONDITIONS, MODERNIZATION_STATES, TEXT_SIGNALS
from fusion_model import fusion_features, FusionModel
from property_models import predict_components_v2
from release_bundle import load_release_bundle
from structured_model import StructuredModel, structured_features
from text_model import TextModel
from tools.export_actvision_fixtures import fixtures


@pytest.mark.parametrize("name", list(fixtures()))
def test_shared_fixtures_validate_without_drift(name):
    actual = json.loads((Path("contracts") / "fixtures" / f"{name}.json").read_text())
    assert actual == fixtures()[name]
    assert validate_contract(actual) == actual


def test_schema_taxonomy_matches_python_authority():
    assert SCHEMA["$defs"]["condition"]["enum"] == list(PHYSICAL_CONDITIONS)
    assert SCHEMA["$defs"]["modernization"]["enum"] == list(MODERNIZATION_STATES)
    assert SCHEMA["$defs"]["text_signal"]["enum"] == list(TEXT_SIGNALS)


@pytest.mark.parametrize("change", [
    lambda p: p.update(schema_version="v1"),
    lambda p: p.update(release_id="different"),
    lambda p: p["evidence"].update(listing_id="changed"),
    lambda p: p["components"]["vision"]["result"].update(physical_condition="C5_REHAB_NEEDED"),
    lambda p: p["result"].update(value_add_score=0),
    lambda p: p.update(status="complete"),
    lambda p: p.update(modalities_used=["text"]),
    lambda p: p["coverage"].update(usable_photo_count=1),
    lambda p: p["coverage"].update(structured_completeness=float("nan")),
    lambda p: p.update(opportunity_score=.1),
])
def test_prediction_rejects_identity_and_unknown_coercion(change):
    value = fixtures()["prediction-unavailable"]
    change(value)
    with pytest.raises(ValueError):
        validate_contract(value)


@pytest.mark.parametrize("field,value", [
    ("YearBuilt", "1963"), ("YearBuilt", True), ("ClosePrice", 123),
    ("PublicRemarks", "turnkey"), ("ListingKey", "private-key"),
])
def test_request_rejects_noncanonical_or_leaking_structured_fields(field, value):
    request = fixtures()["inference-request"]
    request["structured"] = {field: value}
    request["evidence"]["structured_sha256"] = digest(request["structured"])
    request["evidence_id"] = digest(request["evidence"])
    with pytest.raises(ValueError):
        validate_contract(request)


def test_photo_request_uses_real_uri_hash_not_fake_image_hash():
    request = fixtures()["inference-request"]
    uri = "https://fixture.invalid/photo"
    request["evidence"]["selected_photos"] = [{"photo_id": "photo", "sha256": None, "uri_sha256": remarks_digest(uri)}]
    request["photo_inputs"] = [{"photo_id": "photo", "uri": uri}]
    request["evidence_id"] = digest(request["evidence"])
    validate_contract(request)
    request["photo_inputs"][0]["uri"] += "?changed"
    with pytest.raises(ValueError, match="URI hash"):
        validate_contract(request)


def test_text_span_and_unknown_feedback():
    from studio_v2 import validate_text_reviews
    item = {"signal": "clear_slate_or_blank_canvas", "state": "PRESENT",
            "probability": None, "snippet": "vision", "start": 11, "end": 16}
    with pytest.raises(ValueError, match="span"):
        validate_text_reviews([item], "bring your vision")
    item.update(start=11, end=17)
    validate_text_reviews([item], "bring your vision")
    with pytest.raises(ValueError, match="does not match"):
        validate_text_reviews([item], "no supporting evidence")
    with pytest.raises(ValueError, match="Duplicate"):
        validate_text_reviews([item, item], "bring your vision")
    item.update(state="UNKNOWN", probability=0.0)
    with pytest.raises(ValueError, match="UNKNOWN"):
        validate_text_reviews([item], None)


def test_structured_pipeline_is_isolated_and_preserves_numeric_types():
    before = structured_features({"YearBuilt": 1963, "City": "San Diego", "ListPrice": 90, "OriginalListPrice": 100})
    after = structured_features({"YearBuilt": 1963, "City": "San Diego", "ListPrice": 90, "OriginalListPrice": 100,
                                 "PublicRemarks": "fully remodeled", "ClosePrice": 1e9, "ListingId": "TARGET"})
    assert before == after
    assert before["YearBuilt"] == 1963.0 and before["price_reduction_pct"] == .1
    assert before["LivingArea_missing"] == 1
    with pytest.raises(ValueError, match="JSON number"):
        structured_features({"YearBuilt": "1963"})


def test_real_separate_baselines_train_only_known_labels_and_roundtrip(tmp_path):
    labels = [{"physical_condition": "C3_WELL_MAINTAINED" if i % 2 else "C5_REHAB_NEEDED",
               "modernization": "ORIGINAL",
               "text_signals": {"needs_tlc": "ABSENT" if i % 2 else "PRESENT"}}
              for i in range(8)]
    facts = [{"YearBuilt": 2000 if i % 2 else 1960} for i in range(8)]
    remarks = ["lovingly maintained home" if i % 2 else "needs significant repairs" for i in range(8)]
    structured = StructuredModel.fit(facts, labels)
    text = TextModel.fit(remarks, labels)
    assert structured.predict([{}]) == [unknown_result()]
    assert text.predict([""]) == [unknown_result()]
    assert set(structured.vectorizer.get_feature_names_out()).isdisjoint({"lovingly", "repairs"})
    assert "yearbuilt" not in text.vectorizer.vocabulary_
    result = text.predict(["needs significant repairs"])[0]
    assert result["physical_condition"] == "C5_REHAB_NEEDED"
    assert result["modernization"] == "UNKNOWN"  # one class is not a trained task
    assert result["confidence"] is None
    assert result["text_signals"][0]["snippet"] is None  # no fabricated explanation
    joblib.dump({"text": text, "structured": structured}, tmp_path / "new.joblib")
    assert joblib.load(tmp_path / "new.joblib")["text"].predict(remarks) == text.predict(remarks)
    with pytest.raises(ValueError, match="UNKNOWN"):
        TextModel.fit(remarks, [{"text_signals": {"needs_tlc": "UNKNOWN"}} for _ in remarks])
    with pytest.raises(ValueError, match="UNKNOWN"):
        StructuredModel.fit(facts, [{} for _ in facts])


def test_legacy_metadata_pickle_layout_unchanged(tmp_path):
    from v1_models import MetadataClassifier
    metadata = [{"YearBuilt": 1960}, {"YearBuilt": 2020}] * 3
    remarks = ["needs repairs", "turnkey remodeled"] * 3
    model = MetadataClassifier.fit(metadata, remarks, [1, 0] * 3)
    joblib.dump(model, tmp_path / "legacy.joblib")
    restored = joblib.load(tmp_path / "legacy.joblib")
    assert restored.__class__.__module__ == "v1_models"
    np.testing.assert_allclose(restored.predict(metadata, remarks), model.predict(metadata, remarks))


def test_four_component_interface_never_adapts_legacy_targets():
    components = predict_components_v2({}, vectors=[], remarks="needs tlc", structured={"YearBuilt": 1960},
                                       coverage={"usable_photo_count": 0, "text_available": True, "structured_completeness": .1})
    assert set(components) == {"vision", "text", "structured", "fusion"}
    assert components["vision"]["status"] == "missing"
    assert components["text"]["status"] == "unavailable"
    assert all(value["result"] == unknown_result() for value in components.values())
    features = fusion_features(components, {"usable_photo_count": 0, "text_available": True, "structured_completeness": .1})
    assert features["vision_available"] == 0
    assert not any("probabilities" in key for key in features)
    with pytest.raises(ValueError, match="out-of-fold"):
        FusionModel.fit([], [], [], prediction_source="protected_test")


def test_vision_and_structured_adapters_use_real_heads():
    from property_models import PhysicalModelAdapter, VisionEvidenceModel
    from v1_models import MetadataClassifier
    labels = [{"physical_condition": "C3_WELL_MAINTAINED" if i % 2 else "C5_REHAB_NEEDED"} for i in range(8)]
    bags = [[np.array([1., .2]) if i % 2 else np.array([-1., .2])] for i in range(8)]
    vision = VisionEvidenceModel.fit(bags, labels)
    assert vision.backbone == "google/siglip2-base-patch16-224"
    model = StructuredModel.fit([{"YearBuilt": 2000 if i % 2 else 1960} for i in range(8)], labels)
    adapters = {
        "vision": PhysicalModelAdapter("vision", vision, "test-vision", "test-calibration"),
        "structured": PhysicalModelAdapter("structured", model, "test-structured", "test-calibration"),
    }
    components = predict_components_v2(adapters, vectors=bags[0], remarks="", structured={"YearBuilt": 1960},
                                       coverage={"usable_photo_count": 1, "text_available": False, "structured_completeness": 1 / 15})
    assert components["vision"]["status"] == components["structured"]["status"] == "available"
    assert components["vision"]["result"]["physical_condition"] == "C5_REHAB_NEEDED"
    assert components["text"]["status"] == "missing" and components["fusion"]["status"] == "unavailable"
    with pytest.raises(ValueError, match="legacy targets"):
        PhysicalModelAdapter("structured", MetadataClassifier(None, None, None), "v1", "none")


def test_unknown_tag_retraction_and_canonical_events_are_atomic(monkeypatch):
    from cloud_store import SupabaseStore
    from studio_v2 import label_evidence
    signal = {"signal": "needs_tlc", "state": "PRESENT", "probability": None, "snippet": "needs TLC", "start": 0, "end": 9}

    class Database:
        workspace = "test"

        def __init__(self):
            self.saved = {"revision": 1, "text_signals": [signal]}
            self.events = []
            self.fail = False

        @contextmanager
        def connect(self):
            prior = copy.deepcopy((self.saved, self.events))
            try:
                yield self
            except Exception:
                self.saved, self.events = prior
                raise

        def state(self, db, kind, identifier):
            return self.saved if kind == "property" else None

        def save(self, db, kind, identifier, expected, record):
            if expected != self.saved["revision"]:
                raise RuntimeError("changed")
            self.saved = {**record, "revision": expected + 1}
            return self.saved

        def execute(self, sql, params):
            if "INSERT INTO acq_training.review_events" in sql:
                if self.fail:
                    raise OSError("Migration unavailable")
                self.events.append({"task": params[2], "answer": params[3].obj, "reviewer": params[5]})
            return self

        def fetchone(self):
            return None

    class Store(SupabaseStore):
        def _examples(self, db, identifier):
            return [{"id": "example", "listing_key": identifier}]

        def _photos(self, db, identifier):
            return []

        def _history(self, *args):
            return {"blocked": False}

        def property(self, identifier):
            return {"property": {"id": identifier, "mls_remarks": "needs TLC", "metadata": {}}, "images": []}

    db = Database()
    store = Store(db, None)
    body = {
        "kind": "property", "id": "listing", "reviewer": "authenticated-reviewer",
        "status": "approved", "expected_revision": 1, "label_schema_version": "actvision-labels-v2",
        "label_evidence_id": label_evidence("listing", "needs TLC", [], {}),
        "text_signals": [], "physical_condition": "UNKNOWN", "modernization_state": "UNKNOWN",
        "target_fit": "unsure", "reason": "Insufficient evidence",
    }
    db.fail = True
    with pytest.raises(OSError, match="Migration"):
        store.save_review(body)
    assert db.saved["revision"] == 1 and not db.events
    db.fail = False
    assert store.save_review(body)["revision"] == 2
    text_event = next(event for event in db.events if event["task"] == "property_text_signal")
    assert text_event["answer"]["state"] == "UNKNOWN"
    assert text_event["answer"]["probability"] is None
    assert all(event["reviewer"] == "authenticated-reviewer" for event in db.events)
    with pytest.raises(RuntimeError):
        store.save_review(body)


def test_label_fingerprint_changes_for_selection_and_reviews():
    from studio_v2 import label_evidence
    photos = [{"id": "p", "sha256": "a" * 64, "review": {"revision": 0}, "selection": {"included": True}}]
    before = label_evidence("property", "remarks", photos, {"YearBuilt": 1963})
    photos[0]["selection"]["included"] = False
    assert label_evidence("property", "remarks", photos, {"YearBuilt": 1963}) != before


def make_release(tmp_path):
    release = fixtures()["release-candidate"]
    blob = b"synthetic inert artifact"
    (tmp_path / "text.bin").write_bytes(blob)
    release.update(status="shadow", approved_by="operator", approved_at="2026-10-02T01:00:00Z")
    release["evaluation"]["protected_slices_passed"] = True
    release["components"]["text"] = {
        "uri": "text.bin", "sha256": hashlib.sha256(blob).hexdigest(), "framework": "fixture", "framework_version": "1",
        "component_version": "text-1", "feature_schema_version": "actvision-text-tfidf-v2",
        "label_schema_version": "actvision-labels-v2", "calibration_version": "calibration-1",
    }
    path = tmp_path / "release.json"
    path.write_text(json.dumps(release))
    return release, path


def test_release_bundle_checks_bytes_without_deserializing(tmp_path):
    release, path = make_release(tmp_path)
    loaded = load_release_bundle(path, expected_release_id=release["release_id"], frameworks={"fixture": "1"})
    assert loaded.artifacts["text"] == b"synthetic inert artifact"
    (tmp_path / "text.bin").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="SHA256"):
        load_release_bundle(path, frameworks={"fixture": "1"})


@pytest.mark.parametrize("change,match", [
    (lambda r: r.update(status="candidate"), "approved"),
    (lambda r: r.update(approved_by=None), "approval|reviewer"),
    (lambda r: r["evaluation"].update(protected_slices_passed=False), "protected"),
    (lambda r: r["components"]["text"].update(feature_schema_version="metadata-v1"), "feature schema"),
    (lambda r: r["components"]["text"].update(framework_version="2"), "framework"),
    (lambda r: r["components"]["text"].update(uri="..\\other.bin"), "relative"),
    (lambda r: r["components"]["text"].update(uri="https://evil.invalid/file"), "relative"),
])
def test_release_loader_rejects_unapproved_or_incompatible_bundles(tmp_path, change, match):
    release, path = make_release(tmp_path)
    change(release)
    path.write_text(json.dumps(release))
    with pytest.raises(ValueError, match=match):
        load_release_bundle(path, frameworks={"fixture": "1"})


class FeedbackDB:
    workspace = "fixture-workspace"

    def __init__(self):
        self.events = {}
        self.row = None

    @contextmanager
    def connect(self):
        yield self

    def execute(self, sql, params):
        if "INSERT INTO acq_training.actvision_feedback_events" in sql:
            _, event_id, _, _, _, sha, payload = params
            if event_id in self.events:
                self.row = None
            else:
                self.events[event_id] = {"payload_sha256": sha, "payload": payload.obj}
                self.row = {"event_id": event_id}
        elif "SELECT payload_sha256" in sql:
            self.row = {"payload_sha256": self.events[params[1]]["payload_sha256"]}
        else:
            raise AssertionError(sql)
        return self

    def fetchone(self):
        return self.row


def test_feedback_is_append_only_idempotent_and_workspace_bound(monkeypatch):
    from actvision_service import receive_feedback
    monkeypatch.delenv("ACTVISION_SOURCE_WORKSPACE_ID", raising=False)
    database = FeedbackDB()
    store = type("Store", (), {"workspace": database.workspace, "database": database})()
    payload = fixtures()["feedback"]
    assert receive_feedback(store, payload)["status"] == "accepted"
    assert receive_feedback(store, payload)["status"] == "duplicate"
    changed = copy.deepcopy(payload)
    changed["note"] = "changed"
    with pytest.raises(RuntimeError, match="different content"):
        receive_feedback(store, changed)
    assert len(database.events) == 1 and database.events[payload["event_id"]]["payload"] == payload
    monkeypatch.setenv("ACTVISION_SOURCE_WORKSPACE_ID", "different")
    with pytest.raises(PermissionError):
        receive_feedback(store, payload)


def test_hosted_service_auth_and_unavailable_are_explicit(monkeypatch):
    from test_hosted_server import App, auth, request
    from hosted_server import create_server
    monkeypatch.setenv("ACTVISION_SERVICE_TOKEN", "x" * 32)
    monkeypatch.setenv("ACTVISION_SOURCE_WORKSPACE_ID", "fixture-workspace")
    monkeypatch.delenv("ACTVISION_RELEASE_MANIFEST", raising=False)
    server = create_server(0, App(), auth(monkeypatch))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        port = server.server_address[1]
        body = json.dumps(fixtures()["inference-request"])
        assert request(port, "POST", "/api/actvision/v2/infer", body)[0] == 401
        status, _, response = request(port, "POST", "/api/actvision/v2/infer", body,
                                      {"Authorization": "Bearer " + "x" * 32})
        assert status == 503 and json.loads(response)["code"] == "actvision_unavailable"
        assert request(port, "POST", "/api/actvision/v2/infer", "{}",
                       {"Authorization": "Bearer " + "x" * 32})[0] == 400
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_operator_restriction_and_no_training_on_label(monkeypatch):
    import studio_v2
    monkeypatch.setenv("STUDIO_ROLE", "reviewer")
    with pytest.raises(PermissionError):
        studio_v2.require_operator()
    monkeypatch.setenv("STUDIO_ROLE", "operator")
    studio_v2.require_operator()
    from workbench_training import labels
    with pytest.raises(ValueError, match="UNKNOWN"):
        labels([{"target_label": "UNKNOWN"}])


def test_hosted_freeze_and_training_cannot_bypass_role_through_legacy_paths(monkeypatch):
    from test_hosted_server import App, auth, request
    from hosted_server import create_server, COOKIE
    monkeypatch.setenv("STUDIO_ROLE", "reviewer")
    configured_auth = auth(monkeypatch)
    app = App()
    server = create_server(0, app, configured_auth)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        headers = {"Cookie": COOKIE + "=" + configured_auth.issue(),
                   "Origin": configured_auth.origin, "X-Review-Token": app.token}
        for path in ("/api/studio/workbench/train", "/api/studio/workbench/dataset/freeze", "/api/studio/v2/train"):
            assert request(server.server_address[1], "POST", path, '{"confirmed":true}', headers)[0] == 403
        assert not app.studio.saved
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_local_primary_route_preserves_theme_and_legacy_property_links():
    from types import SimpleNamespace
    from urllib.request import urlopen
    from review_server import create_server
    server = create_server(0, SimpleNamespace(token="test"))
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        base = "http://127.0.0.1:" + str(server.server_address[1])
        for path in ("/", "/?scoutTheme=dark", "/advanced"):
            with urlopen(base + path) as response:
                assert b"<title>ActVision Training Studio</title>" in response.read()
        with urlopen(base + "/?property=listing") as response:
            assert b"<title>ActVision Training Studio</title>" not in response.read()
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


def test_new_label_truth_enters_freeze_unknown_is_excluded_and_test_stays_protected(monkeypatch):
    import model_workbench
    from test_model_workbench import Store, property_row
    from studio_v2 import label_evidence
    store = Store()
    detail = {"property": {"id": "negative", "mls_remarks": "original", "metadata": {}}, "images": []}
    store.property = lambda identifier: detail
    props = [property_row("negative", "g1", "test"), property_row("unsure", "g2")]
    props[0]["review"] = {
        "status": "approved", "label_schema_version": "actvision-labels-v2",
        "target_fit": "not_target", "physical_condition": "C3_WELL_MAINTAINED",
        "modernization_state": "ORIGINAL", "text_signals": [],
        "label_evidence_id": label_evidence("negative", "original", [], {}), "revision": 1,
    }
    props[1]["review"] = {**props[0]["review"], "target_fit": "unsure"}
    monkeypatch.setattr("cloud_training.snapshot", lambda store: ([], props))
    monkeypatch.setattr(model_workbench, "feedback_snapshot", lambda store: {})
    preview = model_workbench.dataset_preview(store)
    assert preview["counts"]["not_targets"] == 1 and preview["counts"]["targets"] == 0
    assert preview["examples"][0]["split"] == "test"
    assert preview["excluded_typed_reviews"][0]["reason"] == "Acquisition fit UNKNOWN"
    props[0]["review"]["target_fit"] = "target"
    with pytest.raises(ValueError, match="changed"):
        model_workbench.freeze_dataset(store, {"id": preview["id"], "confirmed": True})


def test_migration_does_not_grant_feedback_mutation_or_release_promotion():
    sql = (Path("supabase") / "migrations" / "20261002210000_actvision_v2_contracts.sql").read_text()
    assert "before update or delete on acq_training.actvision_feedback_events" in sql
    assert "grant update" not in sql
    assert "alter table acq_training.dataset_items" not in sql

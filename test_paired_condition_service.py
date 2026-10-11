import base64
import copy
import hashlib
import http.client
import json
import sys
import threading
from types import SimpleNamespace
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from paired_condition_service import (
    CANDIDATE_SHA256,
    MANIFEST_SHA256,
    POLICY_VERSION,
    RELEASE_ID,
    PairedConditionService,
    validate_paired,
)


def digest_bytes(value):
    return hashlib.sha256(value).hexdigest()


def decision(label="INSUFFICIENT_EVIDENCE", strength=None, affinity=None):
    return {"decision": label, "strength": strength, "affinity": affinity}


def release_payload():
    return {
        "schema_version": "actvision-paired-condition-v1",
        "release_id": RELEASE_ID,
        "candidate_sha256": CANDIDATE_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "evidence_policy_version": POLICY_VERSION,
        "mode": "development",
        "production_ready": False,
        "encoders": {
            "vision_revision": "google/siglip2-base-patch16-224@pinned",
            "text_checkpoint_sha256": "1" * 64,
        },
        "strength_meaning": "uncalibrated target strength, not probability or opportunity",
    }


def request_payload(*, photos=True):
    content = b"first image bytes"
    return {
        "schema_version": "actvision-paired-condition-v1",
        "request_id": str(uuid4()),
        "idempotency_key": "",
        "release_id": RELEASE_ID,
        "manifest_sha256": MANIFEST_SHA256,
        "workspace_id": "mls-development",
        "evidence_id": "2" * 64,
        "requested_at": datetime.now(timezone.utc).isoformat(),
        "listing": {
            "provider": "trestle",
            "listing_key": "listing-1",
            "listing_id": "MLS-1",
            "physical_property_id": str(uuid4()),
        },
        "evidence": {
            "captured_at": datetime.now(timezone.utc).isoformat(),
            "media_generation": "generation-1",
            "media_timestamp": datetime.now(timezone.utc).isoformat(),
            "public_remarks": "Bring your vision to this dated home.",
            "private_remarks": "Gate code 1234. Original kitchen needs repair. Call 555-111-2222.",
            "structured": {"YearBuilt": 1968, "PropertyType": "Residential"},
            "photos": [
                {
                    "photo_id": "photo-1",
                    "provider_media_id": "media-1",
                    "uri": "https://images.example/one.jpg",
                    "uri_sha256": digest_bytes(b"https://images.example/one.jpg"),
                    "content_sha256": digest_bytes(content),
                    "content_base64": base64.b64encode(content).decode(),
                    "room": "kitchen",
                    "exclusion": "none",
                }
            ]
            if photos
            else [],
        },
    }


def prediction_payload():
    unavailable = decision()
    return {
        "schema_version": "actvision-paired-condition-v1",
        "prediction_id": str(uuid4()),
        "request_id": str(uuid4()),
        "evidence_id": "2" * 64,
        "release": release_payload(),
        "status": "partial",
        "image": unavailable,
        "remarks": {"decision": "TARGET", "strength": 60, "affinity": 0.6},
        "structured": unavailable,
        "metadata": {"decision": "TARGET", "strength": 60, "affinity": 0.6},
        "overall": {"decision": "TARGET", "strength": 60, "affinity": 0.6},
        "learned_prediction": {
            "image": unavailable,
            "remarks": unavailable,
            "structured": unavailable,
            "metadata": unavailable,
            "overall": unavailable,
        },
        "evidence_policy": {
            "version": POLICY_VERSION,
            "reason": "Explicit dated condition evidence.",
            "signals": [],
            "condition_signal": "dated_or_rehab_needed",
            "conflict": False,
            "needs_review": False,
            "automated": True,
            "not_human_review": True,
        },
        "photo_evidence": [],
        "coverage": {"selected_photo_count": 1, "usable_photo_count": 0, "missing_photo_count": 1},
        "modality_errors": [
            {
                "modality": "image",
                "code": "missing_photo_bytes",
                "message": "Selected photo bytes were unavailable.",
                "retryable": True,
            }
        ],
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def feedback_payload(prediction=None):
    prediction = prediction or prediction_payload()
    return {
        "schema_version": "actvision-paired-condition-v1",
        "event_id": str(uuid4()),
        "idempotency_key": "",
        "workspace_id": "mls-development",
        "prediction_id": prediction["prediction_id"],
        "request_id": prediction["request_id"],
        "evidence_id": prediction["evidence_id"],
        "release": prediction["release"],
        "scored_input": {"prediction": prediction},
        "human_labels": {
            "image": decision(),
            "metadata": decision("NOT_TARGET"),
            "overall": decision("TARGET", 75),
        },
        "corrected_photos": [],
        "supporting_remark_quotes": ["Original kitchen needs repair."],
        "notes": "Reviewed against the listing evidence.",
        "reviewer_id": "reviewer@example.test",
        "reviewed_at": datetime.now(timezone.utc).isoformat(),
    }


@pytest.mark.parametrize("kind,payload", [
    ("release", release_payload()),
    ("request", request_payload()),
    ("prediction", prediction_payload()),
    ("feedback", feedback_payload()),
    ("receipt", {
        "schema_version": "actvision-paired-condition-v1",
        "event_id": str(uuid4()),
        "status": "accepted",
        "inbox_status": "pending",
    }),
])
def test_contract_accepts_strict_paired_payloads(kind, payload):
    if "idempotency_key" in payload:
        payload["idempotency_key"] = (
            payload["request_id"] if kind == "request" else payload["event_id"]
        )
    assert validate_paired(payload, kind) == payload


def test_contract_rejects_unknown_fields_and_legacy_c1_c6_fields():
    payload = release_payload()
    payload["physical_condition"] = "C3"
    with pytest.raises(ValueError, match="Invalid paired-condition release contract"):
        validate_paired(payload, "release")


def test_contract_rejects_unknown_policy_signal_fields():
    payload = prediction_payload()
    payload["evidence_policy"]["signals"] = [
        {
            "source_field": "PublicRemarks",
            "category": "dated",
            "scope": "whole_property",
            "confidence": "high",
            "phrase": "dated",
            "snippet": "dated home",
            "start_offset": 0,
            "end_offset": 5,
            "unexpected": True,
        }
    ]
    with pytest.raises(ValueError, match="Invalid paired-condition prediction contract"):
        validate_paired(payload, "prediction")


@pytest.mark.parametrize("label", ["TARGET", "NOT_TARGET", "INSUFFICIENT_EVIDENCE"])
def test_feedback_accepts_exactly_three_human_labels(label):
    payload = feedback_payload()
    payload["idempotency_key"] = payload["event_id"]
    payload["human_labels"]["overall"] = decision(label, 50 if label == "TARGET" else None)
    assert validate_paired(payload, "feedback") == payload


@pytest.mark.parametrize("strength", [49, 101])
def test_target_strength_is_bounded(strength):
    payload = feedback_payload()
    payload["idempotency_key"] = payload["event_id"]
    payload["human_labels"]["overall"] = decision("TARGET", strength)
    with pytest.raises(ValueError, match="Invalid paired-condition feedback contract"):
        validate_paired(payload, "feedback")


def test_non_target_cannot_have_strength_and_idempotency_must_match():
    payload = feedback_payload()
    payload["idempotency_key"] = payload["event_id"]
    payload["human_labels"]["overall"] = decision("NOT_TARGET", 50)
    with pytest.raises(ValueError):
        validate_paired(payload, "feedback")
    payload["human_labels"]["overall"] = decision("NOT_TARGET")
    payload["idempotency_key"] = str(uuid4())
    with pytest.raises(ValueError, match="Idempotency identity mismatch"):
        validate_paired(payload, "feedback")


def test_partial_prediction_requires_explicit_modality_error():
    payload = prediction_payload()
    payload["modality_errors"] = []
    with pytest.raises(ValueError, match="Partial predictions require explicit modality errors"):
        validate_paired(payload, "prediction")


class Store:
    workspace = "model-development"

    def __init__(self):
        self.documents = {}

    def document(self, key):
        value = self.documents.get(key)
        return copy.deepcopy(value) if value is not None else None

    def save_document(self, key, value, expected_revision):
        current = self.documents.get(key)
        assert (current or {}).get("revision", 0) == expected_revision
        saved = {**copy.deepcopy(value), "revision": expected_revision + 1}
        self.documents[key] = saved
        return copy.deepcopy(saved)


def fake_package():
    release = release_payload()
    return {
        "release": release,
        "bundle": {
            "heads": {"vision": None, "text": None, "structured": None, "fusion": None},
            "metadata_strategy": "legacy_union",
        },
    }


def test_inference_sanitizes_private_remarks_and_keeps_metadata_when_photos_missing(monkeypatch):
    store = Store()
    monkeypatch.setattr("paired_condition_service.load_verified_package", lambda _store: fake_package())
    captured = {}

    def text_encoder(text, identity):
        captured["text"] = text
        return [0.1, 0.2]

    service = PairedConditionService(store, text_encoder=text_encoder)
    payload = request_payload(photos=False)
    payload["idempotency_key"] = payload["request_id"]
    result = service.infer(payload)

    assert result["status"] == "partial"
    assert result["image"]["decision"] == "INSUFFICIENT_EVIDENCE"
    assert result["metadata"]["decision"] == "TARGET"
    assert result["overall"]["decision"] == "TARGET"
    assert result["learned_prediction"]["overall"]["decision"] == "INSUFFICIENT_EVIDENCE"
    assert result["modality_errors"][0]["modality"] == "image"
    assert "Original kitchen needs repair" in captured["text"]
    assert "Gate code" not in captured["text"] and "555-111-2222" not in captured["text"]


def test_photo_cache_uses_content_hash_and_encoder_identity(monkeypatch):
    store = Store()
    monkeypatch.setattr("paired_condition_service.load_verified_package", lambda _store: fake_package())
    calls = []

    def vision_encoder(content, identity):
        calls.append((content, identity))
        return [float(content[0]), 0.5]

    service = PairedConditionService(store, vision_encoder=vision_encoder, text_encoder=lambda *_: [0.1])
    first = request_payload()
    first["idempotency_key"] = first["request_id"]
    service.infer(first)

    second = request_payload()
    second["idempotency_key"] = second["request_id"]
    second["evidence"]["photos"][0]["uri"] = first["evidence"]["photos"][0]["uri"]
    second["evidence"]["photos"][0]["uri_sha256"] = first["evidence"]["photos"][0]["uri_sha256"]
    changed = b"changed bytes at same uri"
    second["evidence"]["photos"][0]["content_base64"] = base64.b64encode(changed).decode()
    second["evidence"]["photos"][0]["content_sha256"] = digest_bytes(changed)
    service.infer(second)

    assert len(calls) == 2
    assert calls[0][0] != calls[1][0]


def test_feedback_inbox_is_idempotent_and_conflicting_reuse_fails(monkeypatch):
    store = Store()
    monkeypatch.setattr("paired_condition_service.load_verified_package", lambda _store: fake_package())
    service = PairedConditionService(store)
    payload = feedback_payload()
    payload["idempotency_key"] = payload["event_id"]

    assert service.receive_feedback(payload)["status"] == "accepted"
    assert service.receive_feedback(payload)["status"] == "duplicate"
    saved = store.document("paired-condition-feedback-inbox:" + payload["event_id"])
    assert saved["status"] == "pending"
    assert saved["event"] == payload

    changed = copy.deepcopy(payload)
    changed["notes"] = "different immutable event"
    with pytest.raises(RuntimeError, match="different content"):
        service.receive_feedback(changed)


def test_concurrent_identical_inference_reuses_completed_prediction(monkeypatch):
    class ConcurrentStore(Store):
        raced = False

        def save_document(self, key, value, expected_revision):
            if key.startswith("paired-condition-prediction:") and not self.raced:
                self.raced = True
                self.documents[key] = {**copy.deepcopy(value), "revision": 1}
                raise RuntimeError("optimistic concurrency conflict")
            return super().save_document(key, value, expected_revision)

    store = ConcurrentStore()
    monkeypatch.setattr("paired_condition_service.load_verified_package", lambda _store: fake_package())
    service = PairedConditionService(store, text_encoder=lambda *_: [0.1])
    payload = request_payload(photos=False)
    payload["idempotency_key"] = payload["request_id"]

    first = service.infer(payload)
    second = service.infer(payload)

    assert first == second
    assert store.document("paired-condition-prediction:" + payload["request_id"])[
        "prediction"
    ] == first


def test_hosted_paired_routes_require_dedicated_auth_and_workspace(monkeypatch):
    monkeypatch.setitem(sys.modules, "cloud_runtime", SimpleNamespace(from_env=lambda: None))
    from hosted_server import HostedAuth, create_server
    import paired_condition_service as service_module

    monkeypatch.setenv("STUDIO_PUBLIC_ORIGIN", "https://studio.example.test")
    monkeypatch.setenv("STUDIO_LOGIN_USERNAME", "owner@example.test")
    monkeypatch.setenv("STUDIO_LOGIN_PASSWORD", "correct horse battery")
    monkeypatch.setenv("STUDIO_SESSION_SECRET", "s" * 32)
    monkeypatch.setenv("PAIRED_CONDITION_SERVICE_TOKEN", "p" * 32)
    monkeypatch.setenv("PAIRED_CONDITION_SOURCE_WORKSPACE_ID", "mls-development")
    monkeypatch.setenv("PAIRED_CONDITION_MODE", "development")
    monkeypatch.setenv("PAIRED_CONDITION_RELEASE_ID", RELEASE_ID)
    monkeypatch.setenv("PAIRED_CONDITION_MANIFEST_SHA256", MANIFEST_SHA256)
    monkeypatch.setattr(service_module, "approved_release", lambda _store, _release: release_payload())
    monkeypatch.setattr(service_module, "infer", lambda _store, _payload: prediction_payload())
    monkeypatch.setattr(
        service_module,
        "receive_feedback",
        lambda _store, payload: {
            "schema_version": "actvision-paired-condition-v1",
            "event_id": payload["event_id"],
            "status": "accepted",
            "inbox_status": "pending",
        },
    )

    studio = type("Studio", (), {"store": Store()})()
    app = type("App", (), {"get_studio": lambda self: studio})()
    server = create_server(0, app, HostedAuth())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]

    def request(method, path, payload=None, token=True):
        connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
        headers = {"Host": "studio.example.test"}
        if token:
            headers["Authorization"] = "Bearer " + "p" * 32
        body = json.dumps(payload).encode() if payload is not None else None
        if body is not None:
            headers["Content-Type"] = "application/json"
        connection.request(method, path, body=body, headers=headers)
        response = connection.getresponse()
        result = response.status, json.loads(response.read())
        connection.close()
        return result

    try:
        assert request(
            "GET", "/api/paired-condition/v1/releases/" + RELEASE_ID, token=False
        )[0] == 401
        assert request("GET", "/api/paired-condition/v1/releases/" + RELEASE_ID)[0] == 200
        payload = request_payload(photos=False)
        payload["idempotency_key"] = payload["request_id"]
        payload["workspace_id"] = "other-workspace"
        assert request("POST", "/api/paired-condition/v1/infer", payload)[0] == 403
        payload["workspace_id"] = "mls-development"
        assert request("POST", "/api/paired-condition/v1/infer", payload)[0] == 200
        feedback = feedback_payload()
        feedback["idempotency_key"] = feedback["event_id"]
        assert request("POST", "/api/paired-condition/v1/feedback", feedback)[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        thread.join()

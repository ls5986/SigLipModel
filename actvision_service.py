"""Default-off production bridge. No training, promotion or arbitrary URL fetching."""
import os
import time
from datetime import UTC, datetime
from importlib.metadata import PackageNotFoundError, version

from actvision_contract import digest, validate_contract
from release_bundle import load_release_bundle


class UnavailableError(RuntimeError):
    pass


def configured_bundle(release_id=None):
    path = os.environ.get("ACTVISION_RELEASE_MANIFEST")
    if not path:
        raise UnavailableError("No approved ActVision v2 release bundle is configured")
    frameworks = {}
    for name in ("scikit-learn", "torch", "safetensors"):
        try:
            frameworks[name] = version(name)
        except PackageNotFoundError:
            continue
    try:
        return load_release_bundle(path, expected_release_id=release_id, frameworks=frameworks)
    except (ValueError, OSError) as exc:
        raise UnavailableError("Configured release is missing, incompatible or failed integrity/approval checks") from exc


def _model_worker_online(store, threshold_seconds=90):
    heartbeat = store.document("actvision-v2-model-worker") or {}
    try:
        at = datetime.fromisoformat(heartbeat["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        age = (datetime.now(UTC) - at).total_seconds()
    except (KeyError, TypeError, ValueError):
        return False
    return 0 <= age < threshold_seconds and heartbeat.get("status") in {"ready", "running"}


def approved_manifest(store, release_id):
    if store is None:
        raise UnavailableError("ActVision cloud training store is unavailable")
    active = os.environ.get("ACTVISION_ACTIVE_RELEASE_ID", "").strip()
    if not active:
        raise UnavailableError("No approved ActVision release is active")
    if active != release_id:
        raise UnavailableError("Requested release is not the configured active ActVision release")
    from v2_runtime import release_manifest
    try:
        return release_manifest(store, release_id, allowed_statuses=("shadow", "production"))
    except (ValueError, OSError) as exc:
        raise UnavailableError("Active ActVision release is unavailable or incompatible") from exc


def infer(store, payload):
    validate_contract(payload, "inference_request")
    approved_manifest(store, payload["evidence"]["release_id"])
    if not _model_worker_online(store):
        raise UnavailableError("ActVision model worker is offline or stale")
    from v2_inference import enqueue, RESULT_PREFIX, REQUEST_PREFIX
    try:
        existing = enqueue(store, payload)
    except (ValueError, RuntimeError, OSError) as exc:
        raise UnavailableError("ActVision inference could not be queued safely") from exc
    if existing is not None:
        return existing
    timeout = min(55.0, max(1.0, float(os.environ.get("ACTVISION_INFERENCE_WAIT_SECONDS", "45"))))
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = store.document(RESULT_PREFIX + payload["request_id"])
        if result and result.get("status") == "completed":
            return validate_contract(result["prediction"], "prediction")
        request = store.document(REQUEST_PREFIX + payload["request_id"]) or {}
        if request.get("status") == "failed":
            raise UnavailableError("ActVision inference failed; no prediction was fabricated")
        time.sleep(.5)
    raise UnavailableError("ActVision inference is still pending; retry the same request ID")


def receive_feedback(store, payload):
    validate_contract(payload, "feedback")
    workspace = os.environ.get("ACTVISION_SOURCE_WORKSPACE_ID") or store.workspace
    if payload["evidence"]["workspace_id"] != workspace:
        raise PermissionError("Feedback source workspace is not authorized")
    from psycopg.types.json import Jsonb
    sha = digest(payload)
    with store.database.connect() as db:
        row = db.execute("""INSERT INTO acq_training.actvision_feedback_events
            (workspace_id,event_id,source_prediction_id,evidence_id,release_id,payload_sha256,payload)
            VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING event_id""",
            (store.workspace, payload["event_id"], payload["source_prediction_id"], payload["evidence_id"],
             payload["release_id"], sha, Jsonb(payload))).fetchone()
        if not row:
            previous = db.execute("""SELECT payload_sha256 FROM acq_training.actvision_feedback_events
                WHERE workspace_id=%s AND event_id=%s""", (store.workspace, payload["event_id"])).fetchone()
            if not previous or previous["payload_sha256"] != sha:
                raise RuntimeError("Feedback event ID already exists with different content")
    return {"event_id": payload["event_id"], "status": "accepted" if row else "duplicate"}


def feedback_queue(store):
    with store.database.connect() as db:
        rows = db.execute("""SELECT e.event_id,e.payload,e.received_at,d.decision,d.reviewer_id
            FROM acq_training.actvision_feedback_events e
            LEFT JOIN LATERAL (
                SELECT decision,reviewer_id FROM acq_training.actvision_feedback_decisions d
                WHERE d.workspace_id=e.workspace_id AND d.event_id=e.event_id
                ORDER BY created_at DESC,id DESC LIMIT 1
            ) d ON true
            WHERE e.workspace_id=%s ORDER BY e.received_at DESC,e.event_id LIMIT 100""",
            (store.workspace,)).fetchall()
    return {"items": [{**dict(row), "received_at": str(row["received_at"])} for row in rows],
            "notice": "Reviewing corrections does not approve training. Evidence mapping and a later dataset freeze are required."}


def decide_feedback(store, payload, reviewer):
    if payload.get("decision") not in {"reviewed", "rejected"}:
        raise ValueError("Choose reviewed or rejected")
    note = payload.get("note")
    if not isinstance(note, str) or not 1 <= len(note.strip()) <= 4000:
        raise ValueError("A review rationale is required")
    from uuid import UUID
    identifier = str(UUID(payload.get("decision_id", "")))
    with store.database.connect() as db:
        event = db.execute("""SELECT event_id FROM acq_training.actvision_feedback_events
            WHERE workspace_id=%s AND event_id=%s""", (store.workspace, payload.get("event_id"))).fetchone()
        if not event:
            raise ValueError("Unknown feedback event")
        row = db.execute("""INSERT INTO acq_training.actvision_feedback_decisions
            (workspace_id,id,event_id,decision,reviewer_id,note) VALUES (%s,%s,%s,%s,%s,%s)
            ON CONFLICT DO NOTHING RETURNING id""",
            (store.workspace, identifier, payload["event_id"], payload["decision"], reviewer, note)).fetchone()
        if not row:
            previous = db.execute("""SELECT event_id,decision,reviewer_id,note
                FROM acq_training.actvision_feedback_decisions WHERE workspace_id=%s AND id=%s""",
                (store.workspace, identifier)).fetchone()
            if previous != {"event_id": payload["event_id"], "decision": payload["decision"],
                            "reviewer_id": reviewer, "note": note}:
                raise RuntimeError("Decision ID reused with different content")
    return {"event_id": payload["event_id"], "decision": payload["decision"], "training_approved": False}

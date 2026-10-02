"""Default-off production bridge. No training, promotion or arbitrary URL fetching."""
import os
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


def infer(payload):
    validate_contract(payload, "inference_request")
    configured_bundle(payload["evidence"]["release_id"])
    raise UnavailableError("Physical-evidence inference adapters are not installed; legacy target scores cannot substitute")


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

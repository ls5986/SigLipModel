"""Studio-only ActVision v2 candidate predictions. Never an approved MLS release."""
from __future__ import annotations

from collections import defaultdict
from uuid import uuid4

from actvision_contract import digest
from studio_data import now
from structured_model import ALLOWED_FIELDS, CATEGORICAL, NUMERIC
from v2_runtime import predict_local

REQUEST_PREFIX = "actvision-v2-candidate-prediction-request:"
RESULT_PREFIX = "actvision-v2-candidate-prediction-result:"


def _latest_candidate(store):
    with store.database.connect() as db:
        row = db.execute("""
          SELECT id,version,bundle_manifest,evaluation_summary
          FROM acq_training.model_releases
          WHERE workspace_id=%s AND name='actvision-v2' AND status='candidate'
          ORDER BY version DESC LIMIT 1
        """, (store.workspace,)).fetchone()
    return dict(row) if row else None


def _structured(metadata):
    result = {}
    for key in ALLOWED_FIELDS:
        value = (metadata or {}).get(key)
        if value in {None, ""}:
            continue
        if key in NUMERIC and (isinstance(value, bool) or not isinstance(value, (int, float))):
            continue
        if key in CATEGORICAL and not isinstance(value, str):
            continue
        result[key] = value
    return result


def _evidence(store, property_id):
    detail = store.property(property_id)
    prop = detail["property"]
    history = detail.get("historical_source") or {}
    selected = [
        image for image in detail["images"]
        if image.get("selection", {}).get("included", True)
        and image.get("effective", {}).get("context", image.get("provider_context"))
            in {"subject", "subject_interior", "subject_exterior"}
        and not image.get("synthetic_evidence", {}).get("excluded")
    ]
    # Vision is only allowed when the stored acquisition-era photo inventory is verified.
    vision_ids = {image["id"] for image in selected} if history.get("timing_verified") else set()
    with store.database.connect() as db:
        photos = db.execute("""
          SELECT e.listing_key,p.provider_media_key,p.image_sha256,p.storage_bucket,p.storage_object_key
          FROM acq_training.photos p
          JOIN acq_training.examples e
            ON (e.workspace_id,e.id)=(p.workspace_id,p.example_id)
          WHERE e.workspace_id=%s AND e.listing_key=%s
            AND p.revoked_at IS NULL
            AND (p.retention_until IS NULL OR p.retention_until>now())
          ORDER BY p.provider_media_key,p.id
        """, (store.workspace, property_id)).fetchall()
    refs = {}
    for row in photos:
        image_id = property_id + ":" + str(row["provider_media_key"])
        if image_id in vision_ids and image_id not in refs:
            refs[image_id] = dict(row)
    by_id = {image["id"]: image for image in selected}
    photo_refs = [{
        "photo_id": image_id,
        "sha256": row["image_sha256"],
        "storage_bucket": row["storage_bucket"],
        "storage_object_key": row["storage_object_key"],
        "room": by_id[image_id].get("effective", {}).get("room") or "other",
    } for image_id, row in sorted(refs.items())]
    return {
        "property_id": property_id,
        "remarks": prop.get("mls_remarks") or "",
        "structured": _structured(prop.get("metadata") or {}),
        "photos": photo_refs,
        "timing_verified": bool(history.get("timing_verified")),
        "source_blocked": bool(history.get("blocked")),
        "fingerprint": digest({
            "property_id": property_id,
            "remarks": prop.get("mls_remarks") or "",
            "structured": _structured(prop.get("metadata") or {}),
            "photos": [(p["photo_id"], p["sha256"], p["room"]) for p in photo_refs],
            "source_blocked": bool(history.get("blocked")),
        }),
    }


def public_status(store, property_id):
    candidate = _latest_candidate(store)
    request = store.document(REQUEST_PREFIX + property_id) or {"status": "none"}
    result = store.document(RESULT_PREFIX + property_id)
    current = _evidence(store, property_id)
    stale = bool(
        result and candidate and (
            result.get("release_id") != str(candidate["id"])
            or result.get("evidence_fingerprint") != current["fingerprint"]
        )
    )
    return {
        "status": "stale" if stale else request.get("status", "none") if not result else result.get("status", "completed"),
        "candidate": ({
            "release_id": str(candidate["id"]),
            "version": candidate["version"],
            "evaluation": candidate["evaluation_summary"],
        } if candidate else None),
        "prediction": None if stale or not result else result.get("prediction"),
        "evidence": {
            "vision_available": bool(current["photos"]),
            "text_available": bool(current["remarks"].strip()),
            "structured_available": bool(current["structured"]),
            "source_blocked": current["source_blocked"],
            "timing_verified": current["timing_verified"],
        },
    }


def enqueue(store, payload, actor):
    property_id = str(payload.get("id") or "")
    if not property_id:
        raise ValueError("Choose a property")
    candidate = _latest_candidate(store)
    if not candidate:
        raise ValueError("Train an ActVision v2 candidate first")
    evidence = _evidence(store, property_id)
    key = REQUEST_PREFIX + property_id
    previous = store.document(key) or {}
    result = store.document(RESULT_PREFIX + property_id)
    release_id = str(candidate["id"])
    if (
        result
        and result.get("release_id") == release_id
        and result.get("evidence_fingerprint") == evidence["fingerprint"]
        and result.get("status") == "completed"
    ):
        return public_status(store, property_id)
    if previous.get("status") in {"queued", "running"}:
        return public_status(store, property_id)
    store.save_document(key, {
        "status": "queued",
        "property_id": property_id,
        "release_id": release_id,
        "evidence_fingerprint": evidence["fingerprint"],
        "requested_by": actor,
        "queued_at": now(),
    }, previous.get("revision", 0))
    return public_status(store, property_id)


def _photo_paths(store, evidence):
    values = []
    for photo in evidence["photos"]:
        path = store.storage.get(
            photo["storage_bucket"], photo["storage_object_key"], photo["sha256"]
        )
        values.append({
            "path": path, "photo_id": photo["photo_id"],
            "sha256": photo["sha256"], "room": photo["room"],
        })
    return values


def process(store, request, siglip):
    if request.get("status") != "queued":
        return False
    key = REQUEST_PREFIX + request["property_id"]
    active = store.save_document(
        key, {**request, "status": "running", "started_at": now()}, request["revision"]
    )
    try:
        evidence = _evidence(store, request["property_id"])
        if evidence["fingerprint"] != active["evidence_fingerprint"]:
            raise ValueError("Property evidence changed after candidate prediction was queued")
        photos = _photo_paths(store, evidence)
        predicted = predict_local(
            store, siglip, active["release_id"],
            remarks=evidence["remarks"], structured=evidence["structured"],
            photos=photos, selected_photo_count=len(evidence["photos"]),
            allowed_statuses=("candidate",),
        )
        prediction = {
            "prediction_id": str(uuid4()),
            "release_id": active["release_id"],
            "status": predicted["status"],
            "components": predicted["components"],
            "result": predicted["result"],
            "modalities_used": predicted["modalities_used"],
            "coverage": predicted["coverage"],
            "notice": "Research candidate only. This result does not change labels, MLS intelligence, comps or opportunity scoring.",
        }
        result_key = RESULT_PREFIX + request["property_id"]
        previous = store.document(result_key) or {}
        store.save_document(result_key, {
            "status": "completed",
            "release_id": active["release_id"],
            "evidence_fingerprint": evidence["fingerprint"],
            "prediction": prediction,
            "completed_at": now(),
        }, previous.get("revision", 0))
        store.save_document(
            key, {**active, "status": "completed", "completed_at": now()}, active["revision"]
        )
        return True
    except Exception as exc:
        current = store.document(key) or active
        if current.get("status") == "running":
            store.save_document(key, {
                **{k: v for k, v in current.items() if k != "revision"},
                "status": "failed", "failed_at": now(),
                "error_code": type(exc).__name__,
                "error": "Candidate prediction failed; no label or MLS value was changed.",
            }, current["revision"])
        return False


def poll(store, siglip):
    with store.database.connect() as db:
        row = db.execute("""
          SELECT item_id,payload,revision
          FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'actvision-v2-candidate-prediction-request:%%'
            AND payload->>'status'='queued'
          ORDER BY payload->>'queued_at' LIMIT 1
        """, (store.workspace,)).fetchone()
    if not row:
        return False
    return process(store, {**row["payload"], "revision": row["revision"]}, siglip)

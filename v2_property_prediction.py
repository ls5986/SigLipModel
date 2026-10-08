"""Studio-side ActVision v2 candidate predictions from private frozen evidence."""
from __future__ import annotations

from studio_data import now
from v2_dataset import _structured
from v2_runtime import predict_local, release_manifest

PREFIX = "actvision-v2-property-prediction:"


def latest_release(store):
    with store.database.connect() as db:
        row = db.execute("""
          SELECT id,status,version,created_at
          FROM acq_training.model_releases
          WHERE workspace_id=%s AND status IN ('candidate','shadow','production')
          ORDER BY created_at DESC,version DESC LIMIT 1
        """, (store.workspace,)).fetchone()
    return {**dict(row), "id": str(row["id"])} if row else None


def queue(store, payload, actor):
    identifier = str(payload.get("id") or "")
    if not identifier:
        raise ValueError("Open a property first")
    release = latest_release(store)
    if not release:
        raise ValueError("No ActVision v2 candidate has been trained yet")
    detail = store.property(identifier)
    from studio_v2 import label_evidence
    prop = detail["property"]
    evidence_id = label_evidence(
        identifier, prop.get("mls_remarks") or "", detail["images"], prop.get("metadata")
    )
    key = PREFIX + identifier
    previous = store.document(key) or {}
    if (
        previous.get("status") in {"queued", "running"}
        and previous.get("release_id") == release["id"]
        and previous.get("evidence_id") == evidence_id
    ):
        return previous
    return store.save_document(key, {
        "status": "queued", "id": identifier, "release_id": release["id"],
        "evidence_id": evidence_id, "requested_by": actor, "queued_at": now(),
    }, previous.get("revision", 0))


def prediction(store, identifier):
    record = store.document(PREFIX + identifier)
    if not record:
        return {"status": "none", "release": latest_release(store)}
    detail = store.property(identifier)
    from studio_v2 import label_evidence
    prop = detail["property"]
    current = label_evidence(
        identifier, prop.get("mls_remarks") or "", detail["images"], prop.get("metadata")
    )
    if current != record.get("evidence_id"):
        return {**record, "status": "stale", "reason": "Property evidence changed; run the candidate again"}
    return record


def _local_photos(store, detail):
    history = detail.get("historical_source") or {}
    if not history.get("timing_verified") or history.get("blocked"):
        return []
    identifier = detail["property"]["id"]
    with store.database.connect() as db:
        rows = store._photos(db, identifier)
    source = {row["image_id"]: row for row in rows}
    photos = []
    for image in detail["images"]:
        row = source.get(image["id"])
        if not row:
            continue
        if image.get("selection", {}).get("included") is False:
            continue
        context = image.get("effective", {}).get("context", image.get("provider_context"))
        if context not in {"subject", "subject_interior", "subject_exterior"}:
            continue
        if image.get("synthetic_evidence", {}).get("excluded"):
            continue
        path = store.storage.get(
            row["storage_bucket"], row["storage_object_key"], row["image_sha256"]
        )
        photos.append({
            "path": path, "photo_id": image["id"], "sha256": row["image_sha256"],
            "room": image.get("effective", {}).get("room") or "other",
        })
    return photos


def _training_membership(store, release_id, property_id):
    manifest = release_manifest(
        store, release_id, allowed_statuses=("candidate", "shadow", "production")
    )
    with store.database.connect() as db:
        row = db.execute("""
          SELECT g.split
          FROM acq_training.datasets d
          JOIN acq_training.dataset_items i
            ON (i.workspace_id,i.dataset_id)=(d.workspace_id,d.id)
          JOIN acq_training.dataset_groups g
            ON (g.workspace_id,g.dataset_id,g.group_id)=(i.workspace_id,i.dataset_id,i.group_id)
          WHERE d.workspace_id=%s AND d.manifest_sha256=%s
            AND i.label_snapshot->>'property_id'=%s LIMIT 1
        """, (store.workspace, manifest["dataset_sha256"], property_id)).fetchone()
    return row["split"] if row else None


def poll(store, siglip):
    with store.database.connect() as db:
        row = db.execute("""
          SELECT item_id,payload,revision
          FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'actvision-v2-property-prediction:%%'
            AND payload->>'status'='queued'
          ORDER BY payload->>'queued_at' LIMIT 1
        """, (store.workspace,)).fetchone()
    if not row:
        return False
    request = {**row["payload"], "revision": row["revision"]}
    active = store.save_document(
        row["item_id"], {**request, "status": "running", "started_at": now()},
        request["revision"],
    )
    try:
        detail = store.property(request["id"])
        from studio_v2 import label_evidence
        prop = detail["property"]
        current_evidence = label_evidence(
            prop["id"], prop.get("mls_remarks") or "", detail["images"], prop.get("metadata")
        )
        if current_evidence != request["evidence_id"]:
            raise ValueError("Evidence changed")
        photos = _local_photos(store, detail)
        structured = _structured(prop.get("metadata") or {})
        result = predict_local(
            store, siglip, request["release_id"],
            remarks=prop.get("mls_remarks") or "", structured=structured, photos=photos,
            selected_photo_count=len(detail["images"]),
            allowed_statuses=("candidate", "shadow", "production"),
        )
        membership = _training_membership(store, request["release_id"], prop["id"])
        fresh = store.property(prop["id"])
        fresh_prop = fresh["property"]
        if label_evidence(
            fresh_prop["id"], fresh_prop.get("mls_remarks") or "",
            fresh["images"], fresh_prop.get("metadata")
        ) != request["evidence_id"]:
            raise ValueError("Evidence changed")
        store.save_document(row["item_id"], {
            **active, "status": "completed", "completed_at": now(),
            "result": result["result"], "components": result["components"],
            "coverage": result["coverage"], "modalities_used": result["modalities_used"],
            "prediction_status": result["status"], "training_membership": membership,
            "notice": (
                "This physical-home group was in TRAIN; use this as a demonstration, not independent accuracy."
                if membership == "train" else
                "Protected-test result. This prediction did not change its labels or release."
                if membership == "test" else
                "Held-out validation result." if membership == "validation" else
                "Property was not a member of this candidate's frozen dataset."
            ),
        }, active["revision"])
    except Exception as exc:
        current = store.document(row["item_id"]) or active
        if current.get("status") == "running":
            store.save_document(row["item_id"], {
                **{k: v for k, v in current.items() if k != "revision"},
                "status": "failed", "failed_at": now(), "error_code": type(exc).__name__,
                "reason": "ActVision v2 candidate prediction failed; no score was fabricated.",
            }, current["revision"])
        raise
    return True

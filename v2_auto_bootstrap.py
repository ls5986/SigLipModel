"""Explicit one-time hosted bootstrap for the first real ActVision v2 candidate."""
from __future__ import annotations

import os

from studio_data import now

MARKER = "actvision-v2-auto-bootstrap-once"


def maybe_enqueue(store, *, actor="operator:auto-bootstrap"):
    if os.environ.get("STUDIO_V2_AUTO_BOOTSTRAP_ONCE", "false").lower() != "true":
        return {"status": "disabled"}

    marker = store.document(MARKER) or {}
    if marker.get("status") in {"queued", "completed", "failed"}:
        return marker

    from v2_dataset import freeze, preview
    from v2_training import enqueue, public_status

    current_training = public_status(store)["request"]
    if current_training.get("status") in {"queued", "running"}:
        saved = {
            "status": "queued",
            "dataset_id": current_training.get("dataset_id"),
            "training_id": current_training.get("id"),
            "at": now(),
            "reason": "Existing v2 training request reused",
        }
        return store.save_document(MARKER, saved, marker.get("revision", 0))
    if current_training.get("status") == "completed":
        saved = {
            "status": "completed",
            "dataset_id": current_training.get("dataset_id"),
            "training_id": current_training.get("id"),
            "release_id": current_training.get("release_id"),
            "at": now(),
            "reason": "Existing completed candidate reused",
        }
        return store.save_document(MARKER, saved, marker.get("revision", 0))
    if current_training.get("status") == "failed":
        saved = {
            "status": "failed",
            "at": now(),
            "reason": "Previous v2 training failed; auto-bootstrap will not retry it",
        }
        return store.save_document(MARKER, saved, marker.get("revision", 0))

    coverage = preview(store)
    if not coverage.get("trainable"):
        saved = {
            "status": "failed",
            "at": now(),
            "reason": "Current saved labels are not sufficient for a v2 candidate",
            "counts": coverage.get("counts"),
        }
        return store.save_document(MARKER, saved, marker.get("revision", 0))

    latest = store.document("actvision-v2-dataset-latest") or {}
    if latest.get("fingerprint") == coverage["fingerprint"] and latest.get("id"):
        dataset = latest
    else:
        dataset = freeze(store, {"confirmed": True})

    training = enqueue(
        store,
        {"confirmed": True, "dataset_id": dataset["id"]},
        actor,
    )
    request = training["request"]
    saved = {
        "status": "queued",
        "dataset_id": dataset["id"],
        "dataset_fingerprint": dataset["fingerprint"],
        "training_id": request.get("id"),
        "at": now(),
        "reason": "Explicit one-time hosted bootstrap queued",
    }
    return store.save_document(MARKER, saved, marker.get("revision", 0))


def reconcile(store):
    """Update the one-time marker from the durable training request."""
    marker = store.document(MARKER) or {}
    if marker.get("status") != "queued":
        return marker
    from v2_training import public_status
    request = public_status(store)["request"]
    if request.get("id") != marker.get("training_id"):
        return marker
    if request.get("status") == "completed":
        payload = {
            **{k: v for k, v in marker.items() if k != "revision"},
            "status": "completed",
            "release_id": request.get("release_id"),
            "completed_at": now(),
        }
        return store.save_document(MARKER, payload, marker["revision"])
    if request.get("status") == "failed":
        payload = {
            **{k: v for k, v in marker.items() if k != "revision"},
            "status": "failed",
            "failed_at": now(),
            "reason": "ActVision v2 training failed; explicit operator retry required",
        }
        return store.save_document(MARKER, payload, marker["revision"])
    return marker

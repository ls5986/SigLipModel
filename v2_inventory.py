"""Single inventory/status denominator for the ActVision Training Studio."""
from __future__ import annotations

from collections import Counter


def status(store):
    pages = []
    offset = 0
    inventory = None
    while True:
        page = store.queue({
            "scope": "all", "queue": "all", "offset": str(offset), "limit": "40",
        })
        inventory = inventory or page.get("inventory") or {}
        pages.extend(page["items"])
        offset += len(page["items"])
        if not page["items"] or offset >= page["total"]:
            break

    photo_states = Counter(
        (item.get("photo_status") or {}).get("state", "photo_status_unknown")
        for item in pages
    )
    with store.database.connect() as db:
        drafts = db.execute("""
          SELECT count(*) AS count
          FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'typed-label-result:%%'
            AND payload->>'status'='draft'
        """, (store.workspace,)).fetchone()["count"]
        failed_drafts = db.execute("""
          SELECT count(*) AS count
          FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'typed-label-request:%%'
            AND payload->>'status'='failed'
        """, (store.workspace,)).fetchone()["count"]

    training = {
        "eligible_properties": None, "protected_test": None, "excluded": None,
        "dataset_fingerprint": None, "available": False,
    }
    training_error = None
    try:
        from v2_dataset import preview
        current = preview(store)
        training.update({
            "eligible_properties": current["counts"]["properties"],
            "protected_test": current["counts"]["splits"].get("test", 0),
            "excluded": sum(current["counts"]["excluded"].values()),
            "dataset_fingerprint": current["fingerprint"],
            "available": True,
            "modalities": {
                "vision": current["counts"]["vision"],
                "text": current["counts"]["text"],
                "structured": current["counts"]["structured"],
            },
            "origins": current["counts"]["origins"],
        })
    except Exception as exc:
        training_error = type(exc).__name__

    counts = {
        "imported_source_rows": inventory.get("imported_rows"),
        "physical_properties_groups": inventory.get("physical_groups"),
        "matched_mls_listings": inventory.get("matched_listings"),
        "listings_with_retained_photos": sum((item.get("image_count") or 0) > 0 for item in pages),
        "listings_without_retained_photos": sum((item.get("image_count") or 0) == 0 for item in pages),
        "source_conflicts": sum(bool(item.get("blocked")) for item in pages),
        "ai_drafts": int(drafts),
        "failed_ai_drafts": int(failed_drafts),
        "human_reviewed": sum(item.get("status") == "reviewed" for item in pages),
        "training_eligible": training["eligible_properties"],
        "protected_test": training["protected_test"],
        "excluded_from_v2": training["excluded"],
    }
    return {
        "counts": counts,
        "photo_states": dict(photo_states),
        "training": training,
        "training_status_error": training_error,
        "items": [{
            "id": item["id"], "address": item.get("address"),
            "listing_id": item.get("listing_id"),
            "source_conflict": bool(item.get("blocked")),
            "human_review_status": item.get("status"),
            "photo_status": {
                **(item.get("photo_status") or {}),
                "tagged_photo_count": item.get("tagged_photo_count", 0),
                "interior_tagged_count": item.get("interior_tagged_count", 0),
                "bytes_state": "stored_reference_present" if item.get("image_count") else "not_applicable",
            },
        } for item in pages],
        "denominator": "distinct matched MLS listing keys for listing-level counts; imported rows and physical groups are reported separately",
    }

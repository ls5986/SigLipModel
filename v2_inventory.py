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
        cohort = db.execute("""
          SELECT
            count(DISTINCT group_id) AS target_properties,
            count(DISTINCT CASE
              WHEN source_snapshot->'event_map'->>'recovery_status'='mapped'
                THEN source_snapshot->'event_map'->>'acquisition_listing_key'
              WHEN source_snapshot->'event_map'->>'recovery_status'='acquisition_mls_unavailable'
                THEN NULL
              ELSE listing_key
            END) AS acquisition_mls_listings,
            count(DISTINCT group_id) FILTER (
              WHERE source_snapshot->'event_map'->>'recovery_status'='acquisition_mls_unavailable'
            ) AS source_only_acquisitions,
            count(*) FILTER (
              WHERE source_snapshot ? 'event_map'
                AND coalesce(source_snapshot->'event_map'->>'recovery_status','')
                    NOT IN ('mapped','acquisition_mls_unavailable')
            ) AS unresolved_event_maps
          FROM acq_training.examples
          WHERE workspace_id=%s AND listing_key IS NOT NULL
        """, (store.workspace,)).fetchone()
        drafts = db.execute("""
          SELECT count(*) AS count
          FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'typed-label-result:%%'
            AND payload->>'status'='draft'
        """, (store.workspace,)).fetchone()["count"]
        current_drafts = db.execute("""
          WITH acquisition AS (
            SELECT DISTINCT CASE
              WHEN source_snapshot->'event_map'->>'recovery_status'='mapped'
                THEN source_snapshot->'event_map'->>'acquisition_listing_key'
              WHEN source_snapshot->'event_map'->>'recovery_status'='acquisition_mls_unavailable'
                THEN NULL
              ELSE listing_key
            END AS listing_key
            FROM acq_training.examples
            WHERE workspace_id=%s AND listing_key IS NOT NULL
          ), drafts AS (
            SELECT DISTINCT replace(item_id,'typed-label-result:','') AS listing_key
            FROM acq_training.studio_state
            WHERE workspace_id=%s AND kind='document'
              AND item_id LIKE 'typed-label-result:%%'
              AND payload->>'status'='draft'
              AND payload->>'label_schema_version'='actvision-labels-v2'
          )
          SELECT
            count(*) FILTER (WHERE acquisition.listing_key IS NOT NULL) AS acquisition_listings,
            count(*) FILTER (WHERE drafts.listing_key IS NOT NULL) AS ready,
            count(*) FILTER (
              WHERE acquisition.listing_key IS NOT NULL AND drafts.listing_key IS NULL
            ) AS missing
          FROM acquisition
          LEFT JOIN drafts USING(listing_key)
        """, (store.workspace, store.workspace)).fetchone()
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
        "target_properties": int(cohort["target_properties"]),
        "acquisition_mls_listings": int(cohort["acquisition_mls_listings"]),
        "source_only_acquisitions": int(cohort["source_only_acquisitions"]),
        "unresolved_source_issues": int(cohort["unresolved_event_maps"]),
        "matched_mls_listings": inventory.get("matched_listings"),
        "listings_with_retained_photos": sum((item.get("image_count") or 0) > 0 for item in pages),
        "listings_without_retained_photos": sum((item.get("image_count") or 0) == 0 for item in pages),
        "source_conflicts": int(cohort["unresolved_event_maps"]),
        "source_photo_recovery_pending": sum(bool(item.get("needs_photo_match")) for item in pages),
        "ai_drafts": int(drafts),
        "current_acquisition_ai_drafts": int(current_drafts["ready"]),
        "missing_acquisition_ai_drafts": int(current_drafts["missing"]),
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
        "denominator": "target_properties is the imported acquisition cohort. acquisition_mls_listings counts current acquisition-event MLS listings. unresolved_source_issues is the current source-recovery denominator. AI draft counts are separate coverage metrics and do not imply human confirmation is required.",
    }

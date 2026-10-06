"""Single read-only inventory truth for ActVision Studio."""
from __future__ import annotations

from condition_schema import LABEL_SCHEMA_V2


def inventory_status(store):
    queue = store.queue({"scope": "all", "queue": "all", "offset": 0, "limit": 1})
    raw = dict(queue.get("inventory") or {})
    counts = queue.get("counts") or {}
    with store.database.connect() as db:
        ai_drafts = db.execute("""
          SELECT count(*) AS count
          FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'typed-label-result:%%'
            AND payload->>'status'='draft'
            AND payload->>'label_schema_version'=%s
        """, (store.workspace, LABEL_SCHEMA_V2)).fetchone()["count"]
        human_reviewed = db.execute("""
          SELECT count(*) AS count
          FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='property'
            AND payload->>'status'='approved'
            AND payload->>'label_schema_version'=%s
        """, (store.workspace, LABEL_SCHEMA_V2)).fetchone()["count"]
        latest = db.execute("""
          SELECT id,version,manifest_sha256,frozen_at
          FROM acq_training.datasets
          WHERE workspace_id=%s AND name='actvision-v2'
          ORDER BY version DESC LIMIT 1
        """, (store.workspace,)).fetchone()
        frozen_counts = None
        if latest:
            frozen_counts = dict(db.execute("""
              SELECT
                count(*) FILTER (WHERE split='train') AS train_groups,
                count(*) FILTER (WHERE split='validation') AS validation_groups,
                count(*) FILTER (WHERE split='test') AS protected_test_groups
              FROM acq_training.dataset_groups
              WHERE workspace_id=%s AND dataset_id=%s
            """, (store.workspace, latest["id"])).fetchone())

    from v2_dataset import preview
    current = preview(store)
    split_counts = current["counts"].get("splits", {})
    excluded = current["counts"].get("excluded", {})
    return {
        "imported_source_rows": raw.get("imported_rows", 0),
        "physical_properties": raw.get("physical_groups", 0),
        "matched_mls_listings": raw.get("matched_listings", 0),
        "listings_with_photos": counts.get("with_photos", 0),
        "listings_without_photos": counts.get("without_photos", 0),
        "source_conflicts": counts.get("source_conflicts", 0),
        "ai_drafts": ai_drafts,
        "human_reviewed": human_reviewed,
        "training_eligible": current["counts"].get("properties", 0),
        "train_groups": split_counts.get("train", 0),
        "validation_groups": split_counts.get("validation", 0),
        "protected_test_groups": split_counts.get("test", 0),
        "excluded": sum(excluded.values()),
        "excluded_by_reason": excluded,
        "photo_status_counts": counts.get("photo_states", {}),
        "retained_photo_rows": queue.get("photo_count", 0),
        "unresolved_source_rows": raw.get("unresolved_rows", 0),
        "current_dataset_fingerprint": current["fingerprint"],
        "latest_frozen_dataset": ({
            "id": str(latest["id"]),
            "version": latest["version"],
            "fingerprint": latest["manifest_sha256"],
            "frozen_at": str(latest["frozen_at"]),
            "counts": frozen_counts,
        } if latest else None),
        "definitions": {
            "imported_source_rows": "Rows imported from source acquisition workbooks.",
            "physical_properties": "Persisted physical property groups before v2 alias merging.",
            "matched_mls_listings": "Distinct listings matched to imported source rows.",
            "training_eligible": "Current canonical v2 physical-home samples after provenance, source/era and label eligibility checks.",
            "protected_test_groups": "Current deterministic protected-test groups; a frozen dataset remains immutable once created.",
        },
    }

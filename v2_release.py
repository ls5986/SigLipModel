"""Explicit operator release controls. Training workers cannot call these."""
from __future__ import annotations

from uuid import UUID

from v2_runtime import release_manifest


def _release_id(value):
    try:
        return str(UUID(str(value)))
    except (TypeError, ValueError):
        raise ValueError("Choose a valid release") from None


def promote(store, payload, actor):
    release_id = _release_id(payload.get("release_id"))
    target = payload.get("status")
    if payload.get("confirmed") is not True:
        raise ValueError("Confirm the explicit release promotion")
    if target not in {"shadow", "production"}:
        raise ValueError("Choose shadow or production")
    with store.database.connect() as db:
        db.execute(
            "SELECT acq_training.promote_release_v2(%s,%s,%s,%s)",
            (store.workspace, release_id, target, actor),
        )
    return {
        "release_id": release_id,
        "status": target,
        "manifest": release_manifest(store, release_id, allowed_statuses=(target,)),
        "notice": "Release status changed explicitly. Historical predictions and older releases were not deleted.",
    }


def retire(store, payload, actor):
    release_id = _release_id(payload.get("release_id"))
    if payload.get("confirmed") is not True:
        raise ValueError("Confirm retiring this release")
    with store.database.connect() as db:
        db.execute(
            "SELECT acq_training.retire_release_v2(%s,%s,%s)",
            (store.workspace, release_id, actor),
        )
    return {
        "release_id": release_id, "status": "retired",
        "notice": "Release retired. Historical predictions remain stored. Roll back by pinning another approved release ID.",
    }


def approved(store):
    with store.database.connect() as db:
        rows = db.execute("""
          SELECT id,name,version,status,approved_by,approved_at,evaluation_summary
          FROM acq_training.model_releases
          WHERE workspace_id=%s AND status IN ('shadow','production')
          ORDER BY created_at DESC,version DESC
        """, (store.workspace,)).fetchall()
    return [{
        **dict(row), "id": str(row["id"]),
        "approved_at": str(row["approved_at"]) if row["approved_at"] else None,
    } for row in rows]

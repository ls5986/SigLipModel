"""Versioned local review-to-model identity. No network or implicit model loading."""
import hashlib
import json
from pathlib import Path

from pilot import read_json, sha


def fingerprint(rows, properties=None):
    fields = ("id", "property_id", "group_id", "split", "sha256", "physical_key", "room",
              "features", "preference", "preference_room", "photo_context", "label_exclusion",
              "condition_label", "include_in_similarity")
    labels = [{key: row.get(key) for key in fields} for row in sorted(rows, key=lambda r: r["id"])]
    for label in labels:
        if label["preference"] is None:
            label["preference_room"] = None
    property_fields = (
        "id", "physical_key", "group_id", "split", "model_metadata", "review",
        "human_review_revision", "training_allowed", "label_exclusion",
        "known_target", "target_origin", "source_rows", "photo_coverage",
    )
    property_labels = [
        {key: row.get(key) for key in property_fields}
        for row in sorted(properties or [], key=lambda row: row["id"])
    ]
    value = {"photo_policy":"relevant-photos-v2", "images": labels, "properties": property_labels}
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def candidate(root):
    artifacts = root / "artifacts"
    for pointer_name, parent, filename, hash_field in (
        ("studio_candidate_latest.json", "studio_jobs", "studio_heads.joblib", "heads_sha256"),
        ("candidate_latest.json", "candidates", "heads.joblib", "model_sha256"),
    ):
        path = artifacts / pointer_name
        if not path.exists():
            continue
        pointer = read_json(path)
        folder = Path(pointer["folder"]).resolve()
        if not folder.is_relative_to((artifacts / parent).resolve()):
            raise ValueError("Model folder is outside this data root. Reconcile restored paths first.")
        file = folder / filename
        if not file.is_file() or sha(file) != pointer[hash_field]:
            raise ValueError("Saved model is missing or its hash changed")
        return {**pointer, "heads_sha256": pointer[hash_field]}, file
    raise ValueError("Train a reviewed candidate first; the draft baseline is not an ACQ BOT test model.")


def status(jobs):
    rows, _ = jobs._snapshot()
    properties = jobs._property_snapshot() if hasattr(jobs, "_property_snapshot") else []
    current = fingerprint(rows, properties)
    try:
        pointer, _ = candidate(jobs.root)
    except (OSError, KeyError, ValueError) as exc:
        return {"ready": False, "reason": str(exc), "review_fingerprint": current}
    trained = pointer.get("review_fingerprint")
    ready = trained == current
    return {"ready": ready, "version": pointer["version"], "heads_sha256": pointer["heads_sha256"],
            "components": pointer.get("components", {}),
            "component_versions": pointer.get("component_versions", {}),
            "review_fingerprint": current, "trained_review_fingerprint": trained,
            "reason": "Candidate includes the current eligible review snapshot." if ready else
            "Reviews changed or this older candidate has no review fingerprint. Preview local training and train a new candidate.",
            "excluded_photos": sum(bool(row.get("label_exclusion")) for row in rows)}

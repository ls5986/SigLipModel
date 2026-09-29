"""Freeze approved human feedback without changing the original reviews, splits or model."""

from __future__ import annotations

import hashlib
import json
from collections import Counter

from pilot import DATA, FEATURES, read_json, write_json


def build_snapshot(manifest: dict, reviews: dict, batch: list[dict]) -> dict:
    by_id = {row["image_id"]: row for row in manifest["images"]}
    batch_by_id = {row["image_id"]: row for row in batch}
    properties = {row["listing_key"]: row.get("address") for row in batch}
    images = []
    decisions = []
    room_preferences = []
    room_corrections = []
    evaluation_feedback = []
    for identifier, review in reviews.get("room_corrections", {}).items():
        if review.get("status") != "approved":
            continue
        source = by_id.get(identifier)
        if source is None:
            raise ValueError("Room correction refers to unknown image")
        room_corrections.append({
            "image_id": identifier, "listing_key": source["listing_key"],
            "group_id": source["group_id"], "split": source["split"],
            "training_allowed": source["split"] == "train",
            "room": review["room"], "reviewer": review["reviewer"],
            "reviewed_at": review["updated_at"],
            "label_scope": "Room identity only; no inherited feature or property targets",
            "prediction_visible_during_review": True,
        })
    for identifier, review in reviews.get("evaluation_preferences", {}).items():
        if review.get("status") != "approved":
            continue
        source = by_id.get(identifier)
        if source is None or source["split"] not in {"validation", "test"}:
            raise ValueError("Evaluation feedback must reference a protected held-out image")
        evaluation_feedback.append({
            "image_id": identifier, "group_id": source["group_id"], "split": source["split"],
            "room": review["room"], "preference": review["preference"], "reason": review["reason"],
            "reviewer": review["reviewer"], "reviewed_at": review["updated_at"],
            "training_allowed": False, "prediction_visible_during_review": True,
            "independent_blind_evaluation": False,
        })
    for identifier, review in reviews["images"].items():
        if review["status"] != "approved":
            continue
        if identifier not in batch_by_id or identifier not in by_id:
            raise ValueError("Reviewed image is not in the frozen review batch")
        source = by_id[identifier]
        if source["split"] != "train":
            raise ValueError("Refusing to incorporate holdout annotations as training labels")
        images.append({
            "image_id": identifier, "listing_key": source["listing_key"],
            "group_id": source["group_id"], "image_path": source["image_path"],
            "image_sha256": source["sha256"], "split": source["split"],
            "machine_proposal": {
                "room": source["room_label"], "features": source["feature_labels"],
                "source_model": source["source_model"],
            },
            "human_review": {
                "room": review["room"],
                "features": {name: review.get("features", {}).get(name) for name in FEATURES},
                "reviewer": review["reviewer"], "reviewed_at": review["updated_at"],
                "status": "approved", "notes": review.get("notes", ""),
            },
            "room_corrected": source["room_label"] != review["room"],
            "photo_stage": source["photo_stage"],
            "target_fit": None,
            "label_provenance": "Human-approved image labels; property preference is separate",
        })
    for key, review in reviews["properties"].items():
        if review["status"] != "approved":
            continue
        if key not in properties:
            raise ValueError("Reviewed property is outside the authorized batch")
        decisions.append({
            "listing_key": key, "address": properties[key],
            "target_fit": review["target_fit"], "reason": review["reason"],
            "reviewer": review["reviewer"], "reviewed_at": review["updated_at"],
            "notes": review.get("notes", ""), "status": "approved",
            "image_labels_inferred_from_decision": False,
            "investment_success": None,
        })
    for identifier, review in reviews.get("room_preferences", {}).items():
        if review["status"] != "approved":
            continue
        source = by_id.get(identifier)
        if source is None or source["split"] == "test":
            raise ValueError("Learning preferences must refer to the training/validation learning pool")
        room_preferences.append({
            "image_id": identifier, "listing_key": source["listing_key"],
            "group_id": source["group_id"], "image_sha256": source["sha256"],
            "room": review["room"], "room_label_source": review["room_label_source"],
            "preference": review["preference"], "reason": review["reason"],
            "reviewer": review["reviewer"], "reviewed_at": review["updated_at"],
            "scope": "single_photo_renovation_preference",
            "property_target_inferred": False,
            "original_split": source["split"],
            "requires_explicit_group_promotion": source["split"] != "train",
        })
    counts = Counter(row["target_fit"] for row in decisions)
    missing = [key for key in properties if not any(row["listing_key"] == key for row in decisions)]
    features = {
        name: {
            "present": sum(row["human_review"]["features"][name] == 1 for row in images),
            "absent": sum(row["human_review"]["features"][name] == 0 for row in images),
            "unknown": sum(row["human_review"]["features"][name] is None for row in images),
        }
        for name in FEATURES
    }
    return {
        "source_review_revision": reviews["revision"],
        "source_dataset_sha256": manifest["dataset_sha256"],
        "approved_image_labels": images,
        "approved_property_decisions": decisions,
        "approved_room_preferences": room_preferences,
        "room_corrections": room_corrections,
        "evaluation_feedback_only": evaluation_feedback,
        "summary": {
            "approved_images": len(images),
            "reviewed_properties_with_images": len({row["listing_key"] for row in images}),
            "room_corrections": sum(row["room_corrected"] for row in images),
            "known_feature_labels": sum(
                value is not None for row in images for value in row["human_review"]["features"].values()
            ),
            "feature_coverage": features,
            "property_decisions": dict(counts),
            "room_preferences": dict(Counter(row["preference"] for row in room_preferences)),
            "separate_room_corrections": len(room_corrections),
            "evaluation_feedback_not_for_training": len(evaluation_feedback),
            "pending_property_decisions": [
                {"listing_key": key, "address": properties[key]} for key in missing
            ],
            "room_correction_round_ready": bool(images),
            "target_classifier_ready": False,
        },
        "next_step": "Room corrections are authoritative over older image-room annotations, "
        "but do not re-approve their feature tags. Held-out feedback never trains automatically. "
        "Complete remaining property choices only if any are pending. Then use "
        "the room-first view for explicit photo-level work preferences. Room corrections, "
        "visual features and property decisions stay separate; no automatic retraining.",
        "safeguards": {
            "source_reviews_modified": False, "draft_manifest_modified": False,
            "holdout_groups_modified": False, "model_retrained": False,
            "unknown_features_treated_as_negative": False,
            "missing_property_decisions_inferred_as_targets": False,
            "human_reviews_are_independent_test_set": False,
        },
    }


def main() -> None:
    source = DATA / "human_reviews.json"
    before = source.read_bytes()
    reviews = json.loads(before)
    manifest = read_json(DATA / "manifest.json")
    batch = [
        json.loads(line) for line in (DATA / "review_batch_10_properties.jsonl")
        .read_text(encoding="utf-8").splitlines() if line.strip()
    ]
    snapshot = build_snapshot(manifest, reviews, batch)
    digest = hashlib.sha256(before).hexdigest()
    snapshot["source_review_sha256"] = digest
    folder = DATA / "reviewed" / f"v2-revision-{reviews['revision']}-{digest[:10]}"
    path = folder / "approved_labels.json"
    if path.exists():
        if read_json(path) != snapshot:
            raise ValueError("Existing approved snapshot content differs; refusing overwrite")
    else:
        write_json(path, snapshot)
        for name, rows in (
            ("image_labels.jsonl", snapshot["approved_image_labels"]),
            ("property_labels.jsonl", snapshot["approved_property_decisions"]),
            ("room_preferences.jsonl", snapshot["approved_room_preferences"]),
            ("room_corrections.jsonl", snapshot["room_corrections"]),
            ("evaluation_feedback_only.jsonl", snapshot["evaluation_feedback_only"]),
        ):
            (folder / name).write_text(
                "\n".join(json.dumps(row, ensure_ascii=True) for row in rows) + "\n",
                encoding="utf-8",
            )
    write_json(DATA / "reviewed" / "latest.json", {
        "source_revision": reviews["revision"], "snapshot": str(path),
        "summary": snapshot["summary"], "models_unchanged": True,
    })
    print(json.dumps({"snapshot": str(path), "summary": snapshot["summary"],
                      "source_review_changed_during_export": source.read_bytes() != before}, indent=2))


if __name__ == "__main__":
    main()

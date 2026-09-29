"""Explicit versioned promotion of reviewed property groups; original splits stay untouched."""

import copy
import hashlib
import json
from collections import Counter


def latest_preference(identifier: str, reviews: dict) -> dict | None:
    choices = [reviews.get(field, {}).get(identifier) for field in
               ("room_preferences", "evaluation_preferences")]
    choices = [item for item in choices if item]
    return max(choices, key=lambda item: item.get("updated_at", "")) if choices else None


def prepare_learning_data(manifest: dict, reviews: dict) -> tuple[dict, dict, dict]:
    result = copy.deepcopy(manifest)
    labels = copy.deepcopy(reviews)
    by_id = {row["image_id"]: row for row in manifest["images"]}
    reviewed_ids = {
        key for field in ("images", "room_preferences", "room_corrections", "evaluation_preferences")
        for key, item in reviews.get(field, {}).items()
        if item.get("status") in {"approved", "needs_room_review"}
    }
    if reviewed_ids - by_id.keys():
        raise ValueError("Review refers to an image outside the frozen source manifest")
    promoted_groups = {
        by_id[key]["group_id"] for key in reviewed_ids if by_id[key]["split"] != "train"
    }
    changes = []
    for row in result["images"]:
        row["original_split"] = row["split"]
        if row["group_id"] in promoted_groups:
            row["split"] = "train"
            changes.append({"image_id": row["image_id"], "group_id": row["group_id"],
                            "from": row["original_split"], "to": "train"})
    all_preferences = set(reviews.get("room_preferences", {})) | set(
        reviews.get("evaluation_preferences", {})
    )
    labels["room_preferences"] = {
        identifier: latest_preference(identifier, reviews) for identifier in sorted(all_preferences)
    }
    membership = {row["image_id"]: row["split"] for row in result["images"]}
    policy = {
        "mode": "explicit_reviewed_group_promotion",
        "source_dataset_sha256": manifest["dataset_sha256"],
        "review_revision": reviews["revision"],
        "promoted_groups": sorted(promoted_groups), "promoted_images": changes,
        "split_images": dict(Counter(membership.values())),
        "split_groups": {split: len({row["group_id"] for row in result["images"]
                                     if row["split"] == split})
                         for split in ("train", "validation", "test")},
        "partition_sha256": hashlib.sha256(
            json.dumps(membership, sort_keys=True).encode()
        ).hexdigest(),
        "policy": "User authorized previously reviewed holdout feedback to teach the next version. "
        "Move whole physical/duplicate groups, never individual photos. Unreviewed original "
        "test groups remain excluded. Original manifest and past evaluations are preserved. "
        "Promoted groups are no longer unseen evaluation examples for this version.",
    }
    result["learning_partition"] = policy
    return result, labels, policy

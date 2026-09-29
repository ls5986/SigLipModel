"""Versioned local heads from approved room corrections and photo-level work preferences."""

from __future__ import annotations

import argparse
import hashlib
import json
from collections import Counter
from datetime import UTC, datetime
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    accuracy_score,
    balanced_accuracy_score,
    confusion_matrix,
    f1_score,
    precision_score,
    recall_score,
    roc_auc_score,
)
from sklearn.model_selection import StratifiedGroupKFold

import pilot


def approved_room_reviews(manifest: dict, reviews: dict) -> dict:
    """Room corrections override older annotations, but held-out corrections never train."""
    by_id = {row["image_id"]: row for row in manifest["images"]}
    result = {key: dict(value) for key, value in reviews["images"].items()
              if value.get("status") == "approved"}
    for key, review in reviews.get("room_corrections", {}).items():
        if key not in by_id:
            raise ValueError("Room correction refers to unknown image")
        if review.get("status") == "approved" and by_id[key]["split"] == "train":
            result[key] = dict(review)
    return result


def approved_preferences(manifest: dict, reviews: dict) -> tuple[dict, list[dict]]:
    by_id = {row["image_id"]: row for row in manifest["images"]}
    room_reviews = approved_room_reviews(manifest, reviews)
    selected, excluded = {}, []
    for identifier, label in reviews.get("room_preferences", {}).items():
        if label.get("status") != "approved":
            continue
        row = by_id.get(identifier)
        if row is None or row["split"] != "train":
            raise ValueError("Preference refers to an unknown image or protected holdout")
        reason = None
        human_room = room_reviews.get(identifier, {})
        if label["preference"] == "unsure":
            reason = "Uncertain preferences excluded, not converted to No"
        elif label["preference"] not in {"target", "not_target"}:
            raise ValueError("Unsupported preference label")
        elif human_room.get("status") == "approved" and human_room.get("room") != label["room"]:
            reason = "Approved room correction conflicts with preference's room grouping"
        if reason:
            excluded.append({"id": identifier, "room": label["room"], "reason": reason})
            continue
        selected.setdefault(label["room"], []).append({
            "id": identifier, "group_id": row["group_id"], "listing_key": row["listing_key"],
            "label": int(label["preference"] == "target"),
            "reviewer": label["reviewer"], "reviewed_at": label["updated_at"],
            "reason": label["reason"], "room_label_source": label.get("room_label_source"),
        })
    return selected, excluded


def binary_metrics(y: np.ndarray, score: np.ndarray) -> dict:
    prediction = score >= .5
    return {
        "n": len(y), "accuracy": float(accuracy_score(y, prediction)),
        "balanced_accuracy": float(balanced_accuracy_score(y, prediction)),
        "yes_precision": float(precision_score(y, prediction, zero_division=0)),
        "yes_recall": float(recall_score(y, prediction, zero_division=0)),
        "yes_f1": float(f1_score(y, prediction, zero_division=0)),
        "roc_auc": float(roc_auc_score(y, score)) if len(set(y)) == 2 else None,
        "confusion_matrix_no_yes": confusion_matrix(y, prediction, labels=[0, 1]).tolist(),
        "threshold": .5,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--learning", action="store_true",
                        help="Explicitly promote reviewed holdout property groups for this candidate")
    parser.add_argument("--reviews-snapshot", type=Path)
    args = parser.parse_args()
    manifest = pilot.read_json(pilot.DATA / "manifest.json")
    raw_reviews = (args.reviews_snapshot or pilot.DATA / "human_reviews.json").read_bytes()
    source_reviews = json.loads(raw_reviews)
    reviews = source_reviews
    partition = None
    if args.learning:
        from learning_data import prepare_learning_data

        manifest, reviews, partition = prepare_learning_data(manifest, reviews)
    digest = hashlib.sha256(raw_reviews).hexdigest()
    version = f"{'learning' if args.learning else 'reviewed'}-r{reviews['revision']}-{digest[:10]}"
    folder = pilot.ARTIFACTS / "candidates" / version
    if (folder / "heads.joblib").exists():
        pointer = pilot.read_json(pilot.ARTIFACTS / "candidate_latest.json")
        if pointer["version"] == version and (folder / "predictions.json").exists():
            print(json.dumps({"version": version, "status": "already_trained_no_changes"}), flush=True)
            return
        raise ValueError("This exact candidate exists but is not active; refusing overwrite")
    print(json.dumps({"phase": "fit", "version": version,
                      "promoted_groups": len(partition["promoted_groups"]) if partition else 0}),
          flush=True)
    arrays = np.load(pilot.DATA / "embeddings.npz", allow_pickle=False)
    if arrays["dataset_sha256"].item() != manifest["dataset_sha256"]:
        raise ValueError("Dataset and embeddings differ")
    indices = {key: i for i, key in enumerate(arrays["image_ids"].tolist())}
    rows = manifest["images"]
    x = np.stack([arrays["embeddings"][indices[row["image_id"]]] for row in rows])
    lookup = {row["image_id"]: i for i, row in enumerate(rows)}
    baseline = joblib.load(pilot.ARTIFACTS / "silver_heads.joblib")
    room_labels = np.asarray([row["room_label"] for row in rows], dtype=object)
    room_corrections = []
    for identifier, review in approved_room_reviews(manifest, reviews).items():
        if review.get("status") != "approved":
            continue
        if identifier not in lookup or rows[lookup[identifier]]["split"] != "train":
            raise ValueError("Human room approval refers to protected holdout or missing record")
        if review.get("room") not in pilot.ROOM_MAP.values():
            raise ValueError("Invalid approved room label")
        i = lookup[identifier]
        room_corrections.append({
            "id": identifier, "draft": str(room_labels[i]), "human": review["room"],
            "changed": room_labels[i] != review["room"],
        })
        room_labels[i] = review["room"]
    train_mask = np.asarray([row["split"] == "train" for row in rows])
    test_mask = np.asarray([row["split"] == "test" for row in rows])
    training_rows = [row for row in rows if row["split"] == "train"]
    c = pilot.read_json(pilot.ARTIFACTS / "metrics.json")["room"]["chosen_C"]
    room_model = LogisticRegression(C=c, class_weight="balanced", max_iter=1500,
                                    random_state=pilot.SEED)
    room_model.fit(x[train_mask], room_labels[train_mask],
                   sample_weight=pilot.group_weights(training_rows))
    by_room, excluded = approved_preferences(manifest, reviews)
    preference_models, room_reports, skipped = {}, {}, {}
    preference_training = {}
    for room, labels in by_room.items():
        y = np.asarray([row["label"] for row in labels])
        groups = np.asarray([row["group_id"] for row in labels])
        features = x[[lookup[row["id"]] for row in labels]]
        counts = Counter(y.tolist())
        group_counts = {label: len(set(groups[y == label])) for label in (0, 1)}
        if min(counts.get(0, 0), counts.get(1, 0)) < 5 or min(group_counts.values()) < 3:
            skipped[room] = {"reason": "Too few decisive Yes/No labels across independent groups",
                             "labels": dict(counts), "groups": group_counts}
            continue
        folds = min(5, min(group_counts.values()))
        cv = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=pilot.SEED)
        oof = np.full(len(labels), np.nan)
        fold_info = []
        for fold, (tr, te) in enumerate(cv.split(features, y, groups), 1):
            if len(set(y[tr])) != 2:
                raise ValueError("A fold cannot train both preference classes")
            assert not set(groups[tr]) & set(groups[te])
            head = LogisticRegression(C=1.0, class_weight="balanced", max_iter=1500,
                                      random_state=pilot.SEED)
            head.fit(features[tr], y[tr], sample_weight=pilot.group_weights([labels[i] for i in tr]))
            score = head.predict_proba(features[te])[:, list(head.classes_).index(1)]
            oof[te] = score
            fold_info.append({
                "fold": fold, "train_groups": sorted(set(groups[tr])),
                "test_groups": sorted(set(groups[te])), "train_n": len(tr), "test_n": len(te),
                "majority_train_label": int(Counter(y[tr].tolist()).most_common(1)[0][0]),
            })
        assert np.isfinite(oof).all()
        head = LogisticRegression(C=1.0, class_weight="balanced", max_iter=1500,
                                  random_state=pilot.SEED)
        head.fit(features, y, sample_weight=pilot.group_weights(labels))
        preference_models[room] = head
        preference_training[room] = {row["id"]: row for row in labels}
        room_reports[room] = {
            "training_labels": len(labels), "yes": counts.get(1, 0), "no": counts.get(0, 0),
            "independent_groups": len(set(groups)), "evaluation": binary_metrics(y, oof),
            "evaluation_type": "Fixed-C five-or-fewer-fold property-grouped out-of-fold "
            "evaluation on HUMAN room-preference labels; not an untouched external test.",
            "majority_baseline_accuracy": max(counts.values()) / len(labels),
            "folds": fold_info,
            "out_of_fold_predictions": [
                {"id": row["id"], "human_label": row["label"], "group_id": row["group_id"],
                 "score": float(score)} for row, score in zip(labels, oof)
            ],
            "labels": labels,
        }
        print(json.dumps({"phase": "head_complete", "room": room, "labels": len(labels)}), flush=True)
    if not preference_models:
        raise ValueError("No room has enough human labels to train; no candidate published")
    report = {
        "version": version, "trained_at": datetime.now(UTC).isoformat(),
        "review_revision": reviews["revision"], "review_sha256": digest,
        "dataset_sha256": manifest["dataset_sha256"],
        "backbone_revision": baseline["backbone_revision"],
        "room_approvals": len(room_corrections),
        "changed_room_labels": sum(row["changed"] for row in room_corrections),
        "room_label_test_agreement": pilot.metrics(
            room_labels[test_mask], room_model.predict(x[test_mask]), room_model.classes_.tolist(),
        ) if test_mask.any() else None,
        "room_label_test_caution": "Still measured against untouched machine draft room labels, "
        "not independent human room truth. Human corrections affect training set only.",
        "preference_heads": room_reports, "skipped_rooms": skipped, "excluded_preferences": excluded,
        "feature_heads": "Original draft-trained feature heads preserved unchanged",
        "property_target_head": "Not trained: only ten property decisions",
        "calibration": "Raw scores uncalibrated; 0.5 is a fixed baseline boundary",
        "production_ready": False,
        "learning_partition": partition,
        "limitations": [
            "Model learns the reviewer's visible-work preference for a single photo, not whole-property fit.",
            "Can’t tell labels are excluded, not converted into negative examples.",
            "Grouping labels may originate from machine room suggestions; those are not human corrections.",
            "Reviewed examples were selected using the pilot UI; generalization to all inventory is unproven.",
            "Out-of-fold results are preliminary internal validation; no claim of beating GPT.",
            "Changing room heads may change which photographs enter a room group.",
            "No financial, ownership, structural-safety or renovation-cost prediction is made.",
        ],
    }
    folder.mkdir(parents=True)
    pilot.write_json(folder / "review_snapshot.json", source_reviews)
    if partition:
        pilot.write_json(folder / "learning_partition.json", partition)
    pilot.write_json(folder / "metrics.json", report)
    bundle = {
        **baseline, "room_model": room_model, "preference_models": preference_models,
        "preference_training": preference_training, "version": version,
        "caution": "HUMAN-GUIDED LOCAL CANDIDATE: not production-validated.",
    }
    joblib.dump(bundle, folder / "heads.joblib")
    reloaded = joblib.load(folder / "heads.joblib")
    np.testing.assert_array_equal(room_model.predict(x[:5]), reloaded["room_model"].predict(x[:5]))
    all_room = room_model.predict(x)
    saved_predictions = {}
    preference_scores = {
        room: model.predict_proba(x)[:, list(model.classes_).index(1)]
        for room, model in preference_models.items()
    }
    fitted_groups = {
        room: {value["group_id"] for value in labels.values()}
        for room, labels in preference_training.items()
    }
    for i, row in enumerate(rows):
        item = {"room": str(all_room[i]), "preferences": {}, "effective_split": row["split"],
                "original_split": row.get("original_split", row["split"])}
        for room, model in preference_models.items():
            item["preferences"][room] = {
                "score": float(preference_scores[room][i]),
                "used_for_training": row["image_id"] in preference_training[room],
                "property_group_used_for_training": row["group_id"] in fitted_groups[room],
            }
        saved_predictions[row["image_id"]] = item
    pilot.write_json(folder / "predictions.json", saved_predictions)
    pilot.write_json(pilot.ARTIFACTS / "candidate_latest.json", {
        "version": version, "folder": str(folder),
        "review_revision": reviews["revision"], "model_sha256": pilot.sha(folder / "heads.joblib"),
        "production_deployed": False, "baseline_preserved": True,
        "learning_mode": args.learning,
    })
    print(json.dumps({
        "version": version, "room_corrections": report["changed_room_labels"],
        "room_preferences": {room: {key: value for key, value in data.items()
                                   if key in {"training_labels", "yes", "no", "independent_groups",
                                              "evaluation"}}
                             for room, data in room_reports.items()},
        "skipped_rooms": skipped, "excluded_preferences": len(excluded),
    }, indent=2), flush=True)


if __name__ == "__main__":
    main()

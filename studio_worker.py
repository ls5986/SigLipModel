"""Frozen-image feature worker for Studio import prelabeling and approved-label fitting."""

import argparse
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sklearn.base import clone
from sklearn.feature_extraction import DictVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold
from sklearn.pipeline import Pipeline

import pilot
from property_models import (
    metadata_completeness,
    metadata_features,
    normalize_context,
    positive_probability,
    predict_property,
    target_class,
    usable_context,
    vision_vector,
)
from train_reviewed import binary_metrics


def group_data(examples):
    groups = pilot.UnionFind(sorted({row["group_id"] for row in examples}))
    hashes = {}
    identities = {}
    for row in examples:
        if row["sha256"] in hashes:
            groups.union(row["group_id"], hashes[row["sha256"]])
        hashes[row["sha256"]] = row["group_id"]
        identity = row.get("physical_key")
        if identity:
            if identity in identities:
                groups.union(row["group_id"], identities[identity])
            identities[identity] = row["group_id"]
    protected = {groups.find(row["group_id"]) for row in examples if row["split"] == "test"}
    return [groups.find(row["group_id"]) for row in examples], protected


def supervised_indices(examples, values, groups, protected):
    by_hash = {}
    for i, value in enumerate(values):
        if value is not None and groups[i] not in protected:
            by_hash.setdefault(examples[i]["sha256"], []).append(i)
    conflicts = sum(len({values[i] for i in indices}) > 1 for indices in by_hash.values())
    chosen = [indices[0] for indices in by_hash.values()
              if len({values[i] for i in indices}) == 1]
    return chosen, conflicts


def load_starting_bundle():
    from model_loop import candidate
    if (pilot.ARTIFACTS / "studio_candidate_latest.json").exists() or (pilot.ARTIFACTS / "candidate_latest.json").exists():
        pointer, file = candidate(pilot.ARTIFACTS.parent)
        return joblib.load(file), pointer["version"]
    return joblib.load(pilot.ARTIFACTS / "silver_heads.joblib"), "silver"


def training_bundle():
    # Rebuild reviewed heads from current eligible labels. Never retain an old
    # preference head after its labels have been withdrawn or quarantined.
    bundle = joblib.load(pilot.ARTIFACTS / "silver_heads.joblib")
    bundle["preference_models"] = {}
    bundle.pop("context_model", None)
    bundle["property_models"] = {}
    bundle.pop("target_similarity", None)
    return bundle, "silver"


def verify_image_snapshot(examples):
    checked = {}
    for row in examples:
        path = Path(row["path"])
        digest = checked.get(path)
        if digest is None:
            digest = pilot.sha(path)
            checked[path] = digest
        if digest != row["sha256"]:
            raise ValueError("Original image bytes changed; the saved vectors/review snapshot cannot be reused")


def metadata_only_candidate(folder, snapshot):
    """Keep verified targets useful even when every photo set lacks interiors."""
    from target_similarity import fit_reference_index, evaluate_reference_index
    backbone = pilot.read_json(pilot.ARTIFACTS / "backbone.json")
    bundle, parent_version = training_bundle()
    if bundle["backbone_revision"] != backbone["revision"]:
        raise ValueError("Saved head and embedding backbone revisions differ")
    properties = snapshot.get("properties", [])
    bundle["property_models"] = {}
    bundle["target_similarity"] = fit_reference_index(properties, {})
    joblib.dump(bundle, folder / "studio_heads.joblib")
    pilot.write_json(folder / "metrics.json", {
        "kind": "train", "objective": snapshot["objective"], "parent_version": parent_version,
        "backbone_revision": backbone["revision"],
        "reference_properties": len(bundle["target_similarity"]["references"]),
        "protected_evaluation": evaluate_reference_index(bundle["target_similarity"], properties, {}),
        "components": {"property_similarity": True},
        "caution": "Metadata-only candidate: no eligible interior photo evidence.",
    })
    proposals = []
    for prop in properties:
        prediction = predict_property(bundle, [], prop.get("metadata"), "automatic")
        proposals.append({"property_id": prop["id"], "source": "local-siglip-draft",
            "model": "siglip2-local-heads", "run_id": folder.name,
            "prompt_hash": "local-"+backbone["revision"], "model_version": folder.name,
            "images": [], "property": {"summary": "Metadata-only similarity draft.",
                "condition_label": "UNKNOWN", "target_prediction": prediction,
                "limitations": prediction["warnings"]+["No eligible interior photo evidence"]}})
    pilot.write_json(folder / "proposals.json", proposals)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    folder = parser.parse_args().job.resolve()
    if not folder.is_relative_to((pilot.ROOT / "artifacts" / "studio_jobs").resolve()):
        raise ValueError("Unexpected job folder")
    snapshot = pilot.read_json(folder / "snapshot.json")
    examples = [r for r in snapshot["examples"] if not r.get("label_exclusion")] if snapshot.get("objective") == "known-target-similarity-v1" else snapshot["examples"]
    properties = snapshot.get("properties", [])
    if not examples and snapshot.get("objective")=="known-target-similarity-v1" and snapshot["kind"]=="train":
        metadata_only_candidate(folder, snapshot)
        return
    if not examples:
        raise ValueError("Import photos before running a local model job")
    verify_image_snapshot(examples)
    backbone = pilot.read_json(pilot.ARTIFACTS / "backbone.json")
    manifest = pilot.read_json(pilot.DATA / "manifest.json")
    cache = np.load(pilot.DATA / "embeddings.npz", allow_pickle=False)
    indices = {key: i for i, key in enumerate(cache["image_ids"].tolist())}
    vectors = {row["sha256"]: cache["embeddings"][indices[row["image_id"]]]
               for row in manifest["images"]}
    vector_path = pilot.DATA / "studio_vectors.npz"
    if vector_path.exists():
        with np.load(vector_path, allow_pickle=False) as stored:
            if stored["revision"].item() != backbone["revision"]:
                raise ValueError("Studio embedding cache uses a different backbone")
            vectors.update(dict(zip(stored["sha256"].tolist(), stored["vectors"])))
    required = {row["sha256"]: row["path"] for row in examples if row["sha256"] not in vectors}

    def progress(message):
        status = pilot.read_json(folder / "status.json")
        status["detail"] = message
        pilot.write_json(folder / "status.json", status)
        print(message, flush=True)

    if required:
        from PIL import Image, ImageOps
        from transformers import AutoImageProcessor, SiglipVisionModel

        torch = pilot.torch_setup()
        processor = AutoImageProcessor.from_pretrained(backbone["snapshot_path"],
                                                       local_files_only=True, use_fast=False)
        encoder = SiglipVisionModel.from_pretrained(backbone["snapshot_path"],
                                                    local_files_only=True).eval()
        for parameter in encoder.parameters():
            parameter.requires_grad_(False)
        items = list(required.items())
        for start in range(0, len(items), 8):
            images, ids = [], []
            for digest, path in items[start:start + 8]:
                file = Path(path)
                if pilot.sha(file) != digest:
                    raise ValueError("Original image bytes changed; import/review snapshot is stale")
                with Image.open(file) as photo:
                    images.append(ImageOps.exif_transpose(photo).convert("RGB"))
                ids.append(digest)
            with torch.inference_mode():
                values = encoder(**processor(images=images, return_tensors="pt")).pooler_output
                values = torch.nn.functional.normalize(values.float(), dim=-1).cpu().numpy()
            vectors.update(zip(ids, values))
            if start % 80 == 0 or start + 8 >= len(items):
                progress(f"Embedded {min(start + 8, len(items))}/{len(items)} new local photos")
                temporary = vector_path.with_suffix(".tmp.npz")
                keys = sorted(vectors)
                np.savez_compressed(temporary, sha256=np.asarray(keys),
                                    vectors=np.stack([vectors[key] for key in keys]),
                                    revision=np.asarray(backbone["revision"]))
                temporary.replace(vector_path)
        del encoder
    x = np.stack([vectors[row["sha256"]] for row in examples])
    bundle, parent_version = training_bundle() if snapshot["kind"] == "train" else load_starting_bundle()
    if bundle["backbone_revision"] != backbone["revision"]:
        raise ValueError("Saved head and embedding backbone revisions differ")
    groups, protected = group_data(examples)
    fitted = []
    report = {"kind": snapshot["kind"], "backbone_revision": backbone["revision"],
              "protected_groups": len(protected), "human_fit": {}, "skipped": {},
              "parent_version": parent_version, "conflicting_duplicate_labels": {},
              "caution": "Local candidate, not automatically deployed. Fit uses approved labels only; "
              "prelabel proposals never become human truth. Preference is not investment success."}
    property_oof = {}

    def fit_head(name, values, minimum=5):
        chosen, conflicts = supervised_indices(examples, values, groups, protected)
        report["conflicting_duplicate_labels"][name] = conflicts
        y = np.asarray([values[i] for i in chosen])
        count = Counter(y.tolist())
        if name == "room" and set(count) != set(bundle["room_model"].classes_):
            report["skipped"][name] = {
                "reason": "Need approved examples across existing room classes before replacing the room head",
                "counts": dict(count),
            }
            return None
        if len(count) < 2 or min(count.values()) < minimum:
            report["skipped"][name] = {"reason": "Insufficient approved class diversity", "counts": dict(count)}
            return None
        unique_groups = {label: len({groups[i] for i in chosen if values[i] == label}) for label in count}
        if min(unique_groups.values()) < 3:
            report["skipped"][name] = {"reason": "Insufficient independent groups per class"}
            return None
        weights = pilot.group_weights([{"group_id": groups[i]} for i in chosen])
        head = LogisticRegression(C=1, max_iter=1500, class_weight="balanced", random_state=pilot.SEED)
        head.fit(x[chosen], y, sample_weight=weights)
        evaluation = None
        if set(count) == {0, 1}:
            cv = StratifiedGroupKFold(n_splits=min(5, min(unique_groups.values())),
                                     shuffle=True, random_state=pilot.SEED)
            oof = np.full(len(chosen), np.nan)
            local_groups = np.asarray([groups[i] for i in chosen])
            valid = True
            for train, test in cv.split(x[chosen], y, local_groups):
                if len(set(y[train])) < 2:
                    valid = False
                    break
                assert not set(local_groups[train]) & set(local_groups[test])
                model = LogisticRegression(C=1, max_iter=1500, class_weight="balanced", random_state=pilot.SEED)
                model.fit(x[np.asarray(chosen)[train]], y[train],
                          sample_weight=pilot.group_weights([{"group_id": local_groups[i]} for i in train]))
                oof[test] = model.predict_proba(x[np.asarray(chosen)[test]])[:, list(model.classes_).index(1)]
            if valid:
                evaluation = binary_metrics(y, oof)
        report["human_fit"][name] = {"labels": len(chosen), "classes": dict(count),
                                    "groups": len({groups[i] for i in chosen}),
                                    "internal_grouped_validation": evaluation}
        fitted.append(name)
        return head

    def fit_property_model(name, feature_rows, property_rows, model):
        eligible = [
            i for i, row in enumerate(property_rows)
            if row.get("training_allowed") and not row.get("label_exclusion")
            and target_class(row.get("review")) is not None and feature_rows.get(row["id"]) is not None
        ]
        labels = [target_class(property_rows[i]["review"]) for i in eligible]
        counts = Counter(labels)
        if len(counts) < 2 or min(counts.values()) < 5:
            report["skipped"][name] = {
                "reason": "Need at least five decisive property reviews in each class",
                "counts": dict(counts),
            }
            return None
        group_values = [property_rows[i]["group_id"] for i in eligible]
        unique_groups = {
            label: len({group for group, value in zip(group_values, labels) if value == label})
            for label in counts
        }
        if min(unique_groups.values()) < 3:
            report["skipped"][name] = {
                "reason": "Need at least three independent property groups in each class",
                "groups": unique_groups,
            }
            return None
        values = [feature_rows[property_rows[i]["id"]] for i in eligible]
        y = np.asarray(labels)
        local_groups = np.asarray(group_values)
        weights = pilot.group_weights([{"group_id": value} for value in group_values])

        def subset(items, indices):
            if isinstance(items, np.ndarray):
                return items[indices]
            return [items[i] for i in indices]

        def fit(instance, items, labels_, weights_):
            if isinstance(instance, Pipeline):
                instance.fit(items, labels_, classifier__sample_weight=weights_)
            else:
                instance.fit(items, labels_, sample_weight=weights_)

        model_values = np.stack(values) if isinstance(values[0], np.ndarray) else values
        folds = min(5, min(unique_groups.values()))
        cv = StratifiedGroupKFold(n_splits=folds, shuffle=True, random_state=pilot.SEED)
        oof = np.full(len(values), np.nan)
        for train, test in cv.split(np.zeros(len(values)), y, local_groups):
            candidate = clone(model)
            fit(candidate, subset(model_values, train), y[train], weights[train])
            oof[test] = candidate.predict_proba(subset(model_values, test))[
                :, list(candidate.classes_).index(1)
            ]
        fit(model, model_values, y, weights)
        property_oof[name] = {
            property_rows[eligible[i]]["id"]: float(oof[i]) for i in range(len(eligible))
        }
        report["human_fit"][name] = {
            "labels": len(values), "classes": dict(counts),
            "groups": len(set(group_values)),
            "internal_grouped_validation": binary_metrics(y, oof),
        }
        fitted.append(name)
        return model

    if snapshot["kind"] == "train":
        progress("Training lightweight heads from approved corrections; unknown entries are masked")
        head = fit_head("room", [row["room"] for row in examples])
        if head is not None:
            bundle["room_model"] = head
        for feature in pilot.FEATURES:
            model = fit_head("feature:" + feature, [
                int(row["features"][feature]) if row["features"].get(feature) is not None else None
                for row in examples
            ])
            if model is not None:
                bundle["feature_models"][feature] = model
        for room in ("kitchen", "bathroom", "living", "bedroom", "exterior", "outdoor", "other"):
            model = fit_head("preference:" + room, [
                int(row["preference"] == "target") if row["preference"] in {"target", "not_target"}
                and row["preference_room"] == room else None for row in examples
            ])
            if model is not None:
                bundle.setdefault("preference_models", {})[room] = model
        context = fit_head("context", [
            normalize_context(row["photo_context"]) if row.get("photo_context") else None
            for row in examples
        ], minimum=3)
        if context is not None:
            bundle["context_model"] = context
    predicted = bundle["room_model"].predict(x)
    context_predicted = (
        bundle["context_model"].predict(x)
        if bundle.get("context_model") is not None else np.asarray(["unknown"] * len(examples))
    )
    effective_contexts = [
        normalize_context(row.get("photo_context") or context_predicted[i])
        for i, row in enumerate(examples)
    ]
    feature_scores = {name: model.predict_proba(x)[:, list(model.classes_).index(1)]
                      for name, model in bundle["feature_models"].items()}
    preferences = {name: model.predict_proba(x)[:, list(model.classes_).index(1)]
                   for name, model in bundle.get("preference_models", {}).items()}
    indices_by_property = {}
    for index, row in enumerate(examples):
        indices_by_property.setdefault(row["property_id"], []).append(index)
    vision_features = {}
    usable_counts = {}
    for prop in properties:
        indices = [
            index for index in indices_by_property.get(prop["id"], [])
            if not examples[index].get("label_exclusion") and usable_context(effective_contexts[index])
        ]
        pooled = vision_vector(x[indices]) if indices else None
        usable_counts[prop["id"]] = len(indices)
        if pooled is not None:
            vision_features[prop["id"]] = pooled
    metadata_rows = {
        prop["id"]: metadata_features(prop.get("metadata"))
        for prop in properties
        if any(not key.endswith("_missing") for key in metadata_features(prop.get("metadata")))
    }
    if snapshot["kind"] == "train" and snapshot.get("objective") != "known-target-similarity-v1":
        property_models = bundle.setdefault("property_models", {})
        vision = fit_property_model(
            "property:vision", vision_features, properties,
            LogisticRegression(C=1, max_iter=1500, class_weight="balanced",
                               random_state=pilot.SEED),
        )
        if vision is not None:
            property_models["vision"] = vision
        metadata = fit_property_model(
            "property:metadata", metadata_rows, properties,
            Pipeline([
                ("vectorize", DictVectorizer(sparse=True)),
                ("classifier", LogisticRegression(
                    C=1, max_iter=1500, class_weight="balanced", random_state=pilot.SEED,
                )),
            ]),
        )
        if metadata is not None:
            property_models["metadata"] = metadata
        if property_models.get("vision") is not None and property_models.get("metadata") is not None:
            fusion_rows = {}
            for prop in properties:
                identifier = prop["id"]
                if (identifier not in property_oof.get("property:vision", {})
                        or identifier not in property_oof.get("property:metadata", {})):
                    continue
                fusion_rows[identifier] = np.asarray([
                    property_oof["property:vision"][identifier],
                    property_oof["property:metadata"][identifier],
                    np.log1p(usable_counts.get(identifier, 0)),
                    metadata_completeness(prop.get("metadata")),
                ])
            fusion = fit_property_model(
                "property:fusion", fusion_rows, properties,
                LogisticRegression(C=1, max_iter=1500, class_weight="balanced",
                                   random_state=pilot.SEED),
            )
            if fusion is not None:
                property_models["fusion"] = fusion
        protected_rows = [
            prop for prop in properties
            if prop.get("split") == "test" and target_class(prop.get("review")) is not None
        ]
        protected_evaluation = {}
        slices = {
            "all": protected_rows,
            "images_and_metadata": [
                prop for prop in protected_rows
                if prop["id"] in vision_features and prop["id"] in metadata_rows
            ],
            "images_only": [
                prop for prop in protected_rows
                if prop["id"] in vision_features and prop["id"] not in metadata_rows
            ],
            "metadata_only": [
                prop for prop in protected_rows
                if prop["id"] not in vision_features and prop["id"] in metadata_rows
            ],
        }
        from property_models import evaluation_slices
        slices.update(evaluation_slices(properties))
        report["protected_slice_counts"] = {name: len(rows) for name, rows in slices.items()}
        for slice_name, rows in slices.items():
            records = []
            for prop in rows:
                identifier = prop["id"]
                if identifier in vision_features and property_models.get("vision") is not None:
                    records.append((
                        "vision", target_class(prop["review"]),
                        positive_probability(
                            property_models["vision"], vision_features[identifier].reshape(1, -1)
                        ),
                    ))
                if identifier in metadata_rows and property_models.get("metadata") is not None:
                    records.append((
                        "metadata", target_class(prop["review"]),
                        positive_probability(property_models["metadata"], [metadata_rows[identifier]]),
                    ))
                if (identifier in vision_features and identifier in metadata_rows
                        and property_models.get("fusion") is not None):
                    vision_score = positive_probability(
                        property_models["vision"], vision_features[identifier].reshape(1, -1)
                    )
                    metadata_score = positive_probability(
                        property_models["metadata"], [metadata_rows[identifier]]
                    )
                    fusion_row = np.asarray([[
                        vision_score, metadata_score,
                        np.log1p(usable_counts.get(identifier, 0)),
                        metadata_completeness(prop.get("metadata")),
                    ]])
                    records.append((
                        "fusion", target_class(prop["review"]),
                        positive_probability(property_models["fusion"], fusion_row),
                    ))
            component_metrics = {}
            for component in ("vision", "metadata", "fusion"):
                values = [(label, score) for name, label, score in records if name == component]
                if not values:
                    continue
                y = np.asarray([value[0] for value in values])
                scores = np.asarray([value[1] for value in values])
                component_metrics[component] = (
                    binary_metrics(y, scores) if len(set(y.tolist())) == 2
                    else {"n": len(y), "class_counts": dict(Counter(y.tolist())),
                          "threshold": .5,
                          "correct_at_threshold": int(((scores >= .5) == y).sum()),
                          "missed_targets": int(((y == 1) & (scores < .5)).sum()),
                          "false_targets": int(((y == 0) & (scores >= .5)).sum()),
                          "reason": "Single-class slice; ranking and balanced metrics unavailable"}
                )
            protected_evaluation[slice_name] = component_metrics
        report["protected_evaluation"] = protected_evaluation
        report["components"] = {
            "photo_context": "context_model" in bundle,
            "room": "room_model" in bundle,
            "photo_features": sorted(bundle.get("feature_models", {})),
            "photo_preferences": sorted(bundle.get("preference_models", {})),
            "property_vision": "vision" in property_models,
            "property_metadata": "metadata" in property_models,
            "property_fusion": "fusion" in property_models,
        }
        if not fitted:
            raise ValueError("Not enough reviewed examples to fit any head; prior model retained.")
        joblib.dump(bundle, folder / "studio_heads.joblib")
    if snapshot["kind"] == "train" and snapshot.get("objective") == "known-target-similarity-v1":
        from target_similarity import fit_reference_index, evaluate_reference_index
        vectors_by_property = {
            prop["id"]: [x[i] for i in indices_by_property.get(prop["id"], [])
                         if not examples[i].get("label_exclusion") and usable_context(effective_contexts[i])]
            for prop in properties
        }
        bundle["property_models"] = {}
        bundle["target_similarity"] = fit_reference_index(properties, vectors_by_property)
        report["objective"] = snapshot["objective"]
        report["reference_properties"] = len(bundle["target_similarity"]["references"])
        report["protected_evaluation"] = evaluate_reference_index(bundle["target_similarity"], properties, vectors_by_property)
        report["components"] = {"property_similarity":True, "photo_context":"context_model" in bundle,
                                "room":"room_model" in bundle}
        report["caution"] = "Positive-only reference index using frozen SigLIP embeddings and descriptive metadata. No probability or profitability claims."
        joblib.dump(bundle, folder / "studio_heads.joblib")
    grouped = {}
    for i, row in enumerate(examples):
        key = row["property_id"]
        grouped.setdefault(key, {
            "property_id": key, "source": "local-siglip-draft", "model": "siglip2-local-heads",
            "run_id": folder.name, "prompt_hash": "local-" + backbone["revision"],
            "model_version": folder.name if snapshot["kind"] == "train" else parent_version,
            "images": [], "property": {
                "summary": "Local room/feature proposals only; no whole-property condition conclusion.",
                "condition_label": "UNKNOWN", "limitations": ["Human review required"],
            },
        })
        room = str(predicted[i])
        context = effective_contexts[i]
        scores = {name: float(values[i]) for name, values in feature_scores.items()
                  if name not in pilot.FEATURE_ROOMS or room in pilot.FEATURE_ROOMS[name]}
        # Conservative tri-state proposals, never treat uncertain middle as absent.
        feature_labels = {name: True if score >= .8 else False if score <= .2 else None
                          for name, score in scores.items()}
        grouped[key]["images"].append({
            "image_id": row["id"], "sha256": row["sha256"], "room": room, "context": context,
            "usable_for_property": usable_context(context),
            "features": feature_labels if usable_context(context) else {
                name: None for name in feature_labels
            },
            "scores_uncalibrated": scores,
            "room_preference_score": (
                float(preferences[room][i])
                if usable_context(context) and room in preferences else None
            ),
            "provenance": "CLASSIFIED local model proposal; visibility not guaranteed",
        })
    for prop in properties:
        key = prop["id"]
        group = grouped.setdefault(key, {
            "property_id": key, "source": "local-siglip-draft", "model": "siglip2-local-heads",
            "run_id": folder.name, "prompt_hash": "local-" + backbone["revision"],
            "model_version": folder.name if snapshot["kind"] == "train" else parent_version,
            "images": [], "property": {},
        })
        usable_indices = [
            index for index in indices_by_property.get(key, [])
            if usable_context(effective_contexts[index]) and not examples[index].get("label_exclusion")
        ]
        prediction = predict_property(
            bundle, [x[index] for index in usable_indices], prop.get("metadata"), "automatic"
        )
        group["property"] = {
            "summary": "Property ranking draft from available reviewed-model components.",
            "condition_label": "UNKNOWN",
            "target_prediction": prediction,
            "limitations": prediction["warnings"] + ["Human review required"],
        }
    pilot.write_json(folder / "metrics.json", report)
    pilot.write_json(folder / "proposals.json", list(grouped.values()))
    progress("Local predictions generated; waiting for studio to publish editable drafts")


if __name__ == "__main__":
    main()

"""Frozen-image feature worker for Studio import prelabeling and approved-label fitting."""

import argparse
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedGroupKFold

import pilot
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


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--job", type=Path, required=True)
    folder = parser.parse_args().job.resolve()
    if not folder.is_relative_to((pilot.ROOT / "artifacts" / "studio_jobs").resolve()):
        raise ValueError("Unexpected job folder")
    snapshot = pilot.read_json(folder / "snapshot.json")
    examples = snapshot["examples"]
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
        if not fitted:
            raise ValueError("Not enough reviewed examples to fit any head; prior model retained.")
        joblib.dump(bundle, folder / "studio_heads.joblib")
    predicted = bundle["room_model"].predict(x)
    feature_scores = {name: model.predict_proba(x)[:, list(model.classes_).index(1)]
                      for name, model in bundle["feature_models"].items()}
    preferences = {name: model.predict_proba(x)[:, list(model.classes_).index(1)]
                   for name, model in bundle.get("preference_models", {}).items()}
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
        scores = {name: float(values[i]) for name, values in feature_scores.items()
                  if name not in pilot.FEATURE_ROOMS or room in pilot.FEATURE_ROOMS[name]}
        # Conservative tri-state proposals, never treat uncertain middle as absent.
        feature_labels = {name: True if score >= .8 else False if score <= .2 else None
                          for name, score in scores.items()}
        grouped[key]["images"].append({
            "image_id": row["id"], "room": room, "features": feature_labels,
            "scores_uncalibrated": scores,
            "room_preference_score": float(preferences[room][i]) if room in preferences else None,
            "provenance": "CLASSIFIED local model proposal; visibility not guaranteed",
        })
    pilot.write_json(folder / "metrics.json", report)
    pilot.write_json(folder / "proposals.json", list(grouped.values()))
    progress("Local predictions generated; waiting for studio to publish editable drafts")


if __name__ == "__main__":
    main()


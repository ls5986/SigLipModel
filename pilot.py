"""Local frozen-SigLIP draft-label baseline. Does not connect to the MLS application."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import random
import re
import sys
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from config import DATA_ROOT, EVIDENCE_ROOT

ROOT = DATA_ROOT
DATA = ROOT / "data"
ARTIFACTS = ROOT / "artifacts"
SOURCE = EVIDENCE_ROOT
CHECKPOINT = "google/siglip2-base-patch16-224"
SEED = 20260922
FEATURES = (
    "dated_kitchen", "dated_bathroom", "old_flooring", "old_cabinetry",
    "dated_appliances", "dated_lighting", "dated_fixtures", "damaged_surfaces",
    "popcorn_ceiling", "wood_paneling", "clutter_obscures_condition",
    "empty_presentation", "visible_deferred_maintenance",
)
ROOM_MAP = {
    "kitchen": "kitchen", "bathroom": "bathroom", "living": "living", "bedroom": "bedroom",
    "exterior": "exterior", "yard": "outdoor", "pool": "outdoor",
    "other": "other", "flooring_finish": "other", "not_property_photo": "other",
}
FEATURE_ROOMS = {
    "dated_kitchen": {"kitchen"},
    "dated_bathroom": {"bathroom"},
    "old_cabinetry": {"kitchen", "bathroom", "other"},
    "dated_appliances": {"kitchen"},
    "old_flooring": {"kitchen", "bathroom", "living", "bedroom", "other"},
    "dated_lighting": {"kitchen", "bathroom", "living", "bedroom", "other"},
    "dated_fixtures": {"kitchen", "bathroom"},
    "popcorn_ceiling": {"kitchen", "bathroom", "living", "bedroom", "other"},
    "wood_paneling": {"kitchen", "bathroom", "living", "bedroom", "other"},
    "clutter_obscures_condition": {"kitchen", "bathroom", "living", "bedroom", "other"},
    "empty_presentation": {"kitchen", "bathroom", "living", "bedroom", "other"},
}
CAUTION = (
    "UNREVIEWED SILVER-LABEL PILOT: trained/evaluated against previous gpt-4.1-mini "
    "draft annotations, not independent human truth. Metrics measure label imitation, "
    "not superiority to GPT, physical-condition accuracy, acquisition fit or profit. "
    "No production deployment; photo timing and model-training/reuse rights are not independently verified."
)
os.environ["HF_HOME"] = str(ROOT / ".cache" / "huggingface")
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
os.environ["HF_HUB_DISABLE_IMPLICIT_TOKEN"] = "1"
os.environ["HF_HUB_DISABLE_XET"] = "1"
os.environ["TOKENIZERS_PARALLELISM"] = "false"
os.environ["HF_HUB_DOWNLOAD_TIMEOUT"] = "180"
os.environ["HF_HUB_ETAG_TIMEOUT"] = "30"
sys.dont_write_bytecode = True


def timestamp() -> str:
    return datetime.now(UTC).isoformat()


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, indent=2, ensure_ascii=True), encoding="utf-8")
    temporary.replace(path)


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8"))


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(value: Any) -> str:
    words = re.sub(r"[^a-z0-9 ]", " ", str(value or "").casefold()).split()
    aliases = {"street": "st", "avenue": "ave", "road": "rd", "drive": "dr",
               "boulevard": "blvd", "lane": "ln", "court": "ct", "place": "pl"}
    return "".join(aliases.get(word, word) for word in words if word not in {"unit", "apt"})


class UnionFind:
    def __init__(self, values: list[str]) -> None:
        self.parents = {value: value for value in values}

    def find(self, value: str) -> str:
        parent = self.parents[value]
        if parent != value:
            self.parents[value] = self.find(parent)
        return self.parents[value]

    def union(self, left: str, right: str) -> None:
        a, b = self.find(left), self.find(right)
        self.parents[max(a, b)] = min(a, b)


def image_fingerprint(path: Path) -> dict[str, Any]:
    import numpy as np
    from PIL import Image, ImageOps

    with Image.open(path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
        width, height = image.size
        horizontal = np.asarray(image.convert("L").resize((9, 8)))
        vertical = np.asarray(image.convert("L").resize((8, 9)))
        horizontal_hash = int("".join("1" if bit else "0" for bit in
                                     (horizontal[:, 1:] > horizontal[:, :-1]).flatten()), 2)
        vertical_hash = int("".join("1" if bit else "0" for bit in
                                   (vertical[1:, :] > vertical[:-1, :]).flatten()), 2)
    return {"width": width, "height": height, "dhash_horizontal": horizontal_hash,
            "dhash_vertical": vertical_hash}


def supported_feature(feature: dict[str, Any], key: str) -> bool:
    return (
        feature.get("feature") in FEATURES
        and isinstance(feature.get("present"), bool)
        and key in feature.get("media_ids", [])
        and not re.search(r"\b(may be|might be|could be|typically|not visible|not shown)\b",
                          feature.get("detail", ""), re.I)
    )


def prepare() -> None:
    import numpy as np
    from sklearn.model_selection import GroupShuffleSplit

    if (DATA / "manifest.json").exists():
        print("Frozen manifest already exists; retaining its splits and review state.", flush=True)
        return
    records = read_json(SOURCE / "records.json")
    media = read_json(SOURCE / "media.json")["items"]
    visuals = read_json(SOURCE / "visual.json")
    subject_keys = {row["ListingKey"] for row in records["properties"]}
    properties = {str(row["ListingKey"]): row for row in records["comps"] + records["properties"]}
    all_keys = sorted(properties)
    uf = UnionFind(all_keys)
    identity_owner = {}
    for key, prop in properties.items():
        parcel = canonical(prop.get("ParcelNumber"))
        unit = canonical(prop.get("UnitNumber"))
        address = canonical(prop.get("UnparsedAddress"))
        state = canonical(prop.get("StateOrProvince"))
        postal = str(prop.get("PostalCode") or "")[:5]
        ids = []
        if parcel and state:
            ids.append(("parcel", state, parcel, unit))
        if address and postal and state:
            ids.append(("address", state, postal, address, unit))
        for identity in ids:
            if identity in identity_owner:
                uf.union(key, identity_owner[identity])
            identity_owner[identity] = key
    indexed = {}
    missing, invalid = [], []
    for item in media:
        if item.get("download_status") != "downloaded" or item["ListingKey"] not in properties:
            continue
        path = SOURCE / item["local_path"]
        if not path.is_file():
            missing.append(item["MediaKey"])
            continue
        digest = sha(path)
        if digest != item.get("sha256"):
            raise ValueError(f"Image content changed: {item['ListingKey']}/{item['MediaKey']}")
        indexed[(str(item["ListingKey"]), str(item["MediaKey"]))] = {**item, "path": str(path)}
    annotated = []
    excluded_staging = []
    for listing_key, visual in sorted(visuals["listings"].items()):
        if visual.get("status") != "analyzed":
            continue
        for observation in visual.get("images", []):
            media_id = str(observation["media_id"])
            item = indexed.get((listing_key, media_id))
            if item is None:
                continue
            text = observation.get("observation", "")
            if re.search(r"\b(virtually staged|virtual staging|digitally staged)\b", text, re.I):
                excluded_staging.append({"listing_key": listing_key, "media_id": media_id})
                continue
            labels = {}
            label_evidence = {}
            for feature in visual.get("features", []):
                if supported_feature(feature, media_id):
                    labels[feature["feature"]] = int(feature["present"])
                    label_evidence[feature["feature"]] = feature["detail"]
            room = ROOM_MAP.get(observation.get("room"))
            if room is None:
                continue
            try:
                fingerprint = image_fingerprint(Path(item["path"]))
            except (OSError, ValueError) as exc:
                invalid.append({"listing_key": listing_key, "media_id": media_id,
                                "error": type(exc).__name__})
                continue
            annotated.append({
                "image_id": f"{listing_key}:{media_id}", "listing_key": listing_key,
                "media_id": media_id, "sha256": item["sha256"], "image_path": item["path"],
                "image_order": item.get("Order"), "source_role": "subject"
                if listing_key in subject_keys else "comp",
                "room_label": room, "feature_labels": labels, "label_evidence": label_evidence,
                "machine_observation": text, "source_model": visual.get("model", "gpt-4.1-mini"),
                "label_status": "machine_proposed", "human_approved": False,
                "photo_stage": "unknown", "investment_success": None, "target_fit": None,
                **fingerprint,
            })
    hashes = defaultdict(list)
    for row in annotated:
        hashes[row["sha256"]].append(row)
    deduplicated = []
    conflicts = []
    for digest, matches in hashes.items():
        representative = dict(matches[0])
        listing_keys = sorted({row["listing_key"] for row in matches})
        for key in listing_keys[1:]:
            uf.union(listing_keys[0], key)
        room_labels = {row["room_label"] for row in matches}
        if len(room_labels) != 1:
            conflicts.append({"sha256": digest, "room_labels": sorted(room_labels)})
            continue
        merged_features = {}
        for name in FEATURES:
            values = {row["feature_labels"][name] for row in matches if name in row["feature_labels"]}
            if len(values) == 1:
                merged_features[name] = values.pop()
        representative["feature_labels"] = merged_features
        representative["listing_keys"] = listing_keys
        representative["all_source_ids"] = [row["image_id"] for row in matches]
        deduplicated.append(representative)
    near_links = 0
    for i, a in enumerate(deduplicated):
        if a["dhash_horizontal"] in {0, 2**64 - 1}:
            continue
        for b in deduplicated[i+1:]:
            if abs(a["width"] / a["height"] - b["width"] / b["height"]) > .03:
                continue
            if (a["dhash_horizontal"] ^ b["dhash_horizontal"]).bit_count() <= 2 and (
                a["dhash_vertical"] ^ b["dhash_vertical"]
            ).bit_count() <= 2:
                uf.union(a["listing_key"], b["listing_key"])
                near_links += 1
    deduplicated.sort(key=lambda row: row["image_id"])
    for row in deduplicated:
        row["group_id"] = uf.find(row["listing_key"])
    groups = np.asarray([row["group_id"] for row in deduplicated])
    indices = np.arange(len(deduplicated))
    train, remaining = next(GroupShuffleSplit(
        n_splits=1, test_size=.30, random_state=SEED,
    ).split(indices, groups=groups))
    validation_local, test_local = next(GroupShuffleSplit(
        n_splits=1, test_size=.50, random_state=SEED + 1,
    ).split(remaining, groups=groups[remaining]))
    split_by_index = {int(i): "train" for i in train}
    split_by_index.update({int(remaining[i]): "validation" for i in validation_local})
    split_by_index.update({int(remaining[i]): "test" for i in test_local})
    for i, row in enumerate(deduplicated):
        row["split"] = split_by_index[i]
    group_splits = defaultdict(set)
    for row in deduplicated:
        group_splits[row["group_id"]].add(row["split"])
    assert all(len(splits) == 1 for splits in group_splits.values())
    payload_hash = hashlib.sha256(json.dumps(deduplicated, sort_keys=True).encode()).hexdigest()
    manifest = {
        "created_at": timestamp(), "caution": CAUTION, "dataset_sha256": payload_hash,
        "checkpoint": CHECKPOINT, "seed": SEED, "images": deduplicated,
        "source_files": {name: sha(SOURCE / name)
                         for name in ("records.json", "visual.json", "media.json")},
        "split_policy": "Seeded 70/15/15 group split; same parcel+unit/address, exact-image "
        "duplicates and conservative paired-dHash near-duplicates stay together. "
        "Not a temporal or independent-human benchmark; building/market generalization unproven.",
        "label_policy": "Rooms from validated machine image annotations. Features included only "
        "when bool-valued, explicitly citing THIS media ID, without speculative wording. "
        "Unknown feature entries are omitted (masked), never converted to zero. "
        "Source subjects and comp listings are both room/feature examples, not positive/negative investments.",
        "excluded": {"missing_images": missing, "invalid_images": invalid,
                     "staging_mentions": excluded_staging, "exact_duplicate_room_conflicts": conflicts},
        "summary": {
            "labeled_images_before_exact_dedup": len(annotated),
            "usable_unique_images": len(deduplicated),
            "physical_or_duplicate_groups": len(group_splits),
            "unique_listing_keys": len({key for row in deduplicated for key in row["listing_keys"]}),
            "near_duplicate_links": near_links,
            "split_images": dict(Counter(row["split"] for row in deduplicated)),
            "split_groups": dict(Counter(next(iter(splits)) for splits in group_splits.values())),
            "room_labels": dict(Counter(row["room_label"] for row in deduplicated)),
            "known_feature_labels": {name: dict(Counter(row["feature_labels"][name]
                for row in deduplicated if name in row["feature_labels"])) for name in FEATURES},
        },
    }
    write_json(DATA / "manifest.json", manifest)
    review_seed = []
    train_subjects = sorted({
        row["listing_key"] for row in deduplicated
        if row["split"] == "train" and row["source_role"] == "subject"
    })
    random.Random(SEED).shuffle(train_subjects)
    for key in train_subjects[:10]:
        rows = [row for row in deduplicated if row["listing_key"] == key and row["split"] == "train"]
        chosen = []
        for room in ("kitchen", "bathroom", "living", "bedroom", "exterior", "outdoor", "other"):
            chosen.extend([row for row in rows if row["room_label"] == room][:2 if room == "bathroom" else 1])
        for row in chosen[:8]:
            review_seed.append({
                "image_id": row["image_id"], "listing_key": key,
                "address": properties[key].get("UnparsedAddress"), "image_path": row["image_path"],
                "machine_room": row["room_label"], "machine_features": row["feature_labels"],
                "machine_observation": row["machine_observation"],
                "reviewed_room": None, "reviewed_features": {},
                "target_fit": None, "target_reason": None,
                "review_status": "unreviewed", "reviewer": None,
                "instruction": "Confirm/correct room and visible features; leave unseen features "
                "unknown. Target_fit applies to the property, not this photo alone.",
            })
    DATA.mkdir(exist_ok=True)
    (DATA / "review_batch_10_properties.jsonl").write_text(
        "\n".join(json.dumps(row, ensure_ascii=True) for row in review_seed) + "\n", encoding="utf-8",
    )
    write_json(DATA / "review_instructions.json", {
        "scope": "First ten training-set subject properties only; validation/test remain held out",
        "allowed_rooms": sorted(set(ROOM_MAP.values())),
        "feature_labels": list(FEATURES), "feature_values": {"1": "present", "0": "absent",
                                                          "null_or_omitted": "unknown"},
        "status": "NOT imported into training; this first run uses machine labels only",
    })
    print(json.dumps(manifest["summary"], indent=2), flush=True)


def torch_setup():
    import torch

    threads = min(6, os.cpu_count() or 2)
    torch.set_num_threads(threads)
    # This setting is process-wide and PyTorch permits changing it only once.
    # Reusing the frozen encoder after text inference must not reconfigure it.
    if torch.get_num_interop_threads() != min(2, threads):
        torch.set_num_interop_threads(min(2, threads))
    torch.manual_seed(SEED)
    return torch


def checkpoint_snapshot() -> tuple[Path, dict[str, Any]]:
    from huggingface_hub import HfApi, snapshot_download

    info_path = ARTIFACTS / "backbone.json"
    if info_path.exists():
        info = read_json(info_path)
        path = Path(info["snapshot_path"])
        if path.exists():
            return path, info
    metadata = HfApi(token=False).model_info(CHECKPOINT)
    revision = metadata.sha
    path = Path(snapshot_download(
        CHECKPOINT, revision=revision, token=False, max_workers=2,
        allow_patterns=["*.json", "*.safetensors", "*.model", "README.md"],
        cache_dir=str(ROOT / ".cache" / "huggingface" / "hub"),
    ))
    info = {
        "model_id": CHECKPOINT, "revision": revision, "snapshot_path": str(path),
        "downloaded_at": timestamp(), "source": f"https://huggingface.co/{CHECKPOINT}",
        "weights": {p.name: {"bytes": p.stat().st_size, "sha256": sha(p)}
                    for p in path.glob("*.safetensors")},
        "license_from_model_card": "Apache-2.0; separate source-image/reuse rights remain unverified",
    }
    write_json(info_path, info)
    return path, info


def embed(batch_size: int, limit: int | None = None) -> None:
    import numpy as np
    from PIL import Image, ImageOps
    from transformers import AutoImageProcessor, SiglipVisionModel

    torch = torch_setup()
    manifest = read_json(DATA / "manifest.json")
    rows = manifest["images"][:limit] if limit else manifest["images"]
    model_path, info = checkpoint_snapshot()
    print(json.dumps({"event": "loading_backbone", "revision": info["revision"],
                      "images": len(rows), "device": "cpu", "batch_size": batch_size}), flush=True)
    processor = AutoImageProcessor.from_pretrained(model_path, local_files_only=True, use_fast=False)
    model = SiglipVisionModel.from_pretrained(
        model_path, local_files_only=True, torch_dtype=torch.float32,
    ).eval()
    for parameter in model.parameters():
        parameter.requires_grad_(False)
    output = DATA / ("smoke_embeddings.npz" if limit else "embeddings.npz")
    progress_path = output.with_suffix(".progress.json")
    existing = {}
    if output.exists():
        stored = np.load(output, allow_pickle=False)
        if stored["dataset_sha256"].item() != manifest["dataset_sha256"]:
            raise ValueError("Embedding cache belongs to another manifest")
        if stored["backbone_revision"].item() != info["revision"]:
            raise ValueError("Embedding cache belongs to another backbone revision")
        existing = dict(zip(stored["image_ids"].tolist(), stored["embeddings"]))
    for start in range(0, len(rows), batch_size):
        group = rows[start:start + batch_size]
        pending = [row for row in group if row["image_id"] not in existing]
        if pending:
            images = []
            for row in pending:
                if sha(Path(row["image_path"])) != row["sha256"]:
                    raise ValueError(f"Image changed after manifest creation: {row['image_id']}")
                with Image.open(row["image_path"]) as source:
                    images.append(ImageOps.exif_transpose(source).convert("RGB"))
            inputs = processor(images=images, return_tensors="pt")
            with torch.inference_mode():
                vectors = model(**inputs).pooler_output
                vectors = torch.nn.functional.normalize(vectors.float(), dim=-1)
            for row, vector in zip(pending, vectors.cpu().numpy()):
                existing[row["image_id"]] = vector
        if start % (batch_size * 10) == 0 or start + batch_size >= len(rows):
            ids = [row["image_id"] for row in rows if row["image_id"] in existing]
            temporary = output.with_suffix(".tmp.npz")
            np.savez_compressed(
                temporary, image_ids=np.asarray(ids),
                embeddings=np.stack([existing[key] for key in ids]),
                dataset_sha256=np.asarray(manifest["dataset_sha256"]),
                backbone_revision=np.asarray(info["revision"]),
            )
            temporary.replace(output)
            write_json(progress_path, {"completed": len(ids), "total": len(rows),
                                       "updated_at": timestamp(), "device": "cpu"})
            print(json.dumps({"event": "embedded", "completed": len(ids), "total": len(rows)}),
                  flush=True)
    for value in existing.values():
        assert np.isfinite(value).all()
    write_json(ARTIFACTS / ("smoke.json" if limit else "embedding_metadata.json"), {
        "images": len(rows), "dimensions": len(next(iter(existing.values()))),
        "backbone_revision": info["revision"], "dataset_sha256": manifest["dataset_sha256"],
        "device": "cpu", "frozen_backbone": True,
        "images_uploaded": False, "originals_modified": False,
    })


def group_weights(rows: list[dict[str, Any]]):
    import numpy as np

    counts = Counter(row["group_id"] for row in rows)
    weights = np.asarray([1 / counts[row["group_id"]] for row in rows])
    return weights / weights.mean()


def metrics(y, predicted, classes):
    from sklearn.metrics import (
        accuracy_score,
        balanced_accuracy_score,
        classification_report,
        confusion_matrix,
    )

    return {
        "n": len(y), "accuracy_vs_draft_labels": float(accuracy_score(y, predicted)),
        "balanced_accuracy_vs_draft_labels": float(balanced_accuracy_score(y, predicted)),
        "classification_report": classification_report(
            y, predicted, labels=classes, output_dict=True, zero_division=0,
        ),
        "confusion_matrix": confusion_matrix(y, predicted, labels=classes).tolist(),
        "class_order": classes,
    }


def train() -> None:
    import joblib
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import f1_score

    manifest = read_json(DATA / "manifest.json")
    embedded = np.load(DATA / "embeddings.npz", allow_pickle=False)
    if embedded["dataset_sha256"].item() != manifest["dataset_sha256"]:
        raise ValueError("Embeddings do not correspond to current frozen dataset")
    rows = manifest["images"]
    indices = {key: index for index, key in enumerate(embedded["image_ids"].tolist())}
    if len(indices) != len(rows):
        raise ValueError("Not all manifest images were embedded")
    x = np.stack([embedded["embeddings"][indices[row["image_id"]]] for row in rows])
    split = np.asarray([row["split"] for row in rows])
    train_mask, val_mask, test_mask = (split == name for name in ("train", "validation", "test"))
    y = np.asarray([row["room_label"] for row in rows])
    train_rows = [row for row in rows if row["split"] == "train"]
    weight = group_weights(train_rows)
    candidates = []
    for c in (.1, 1.0, 10.0):
        model = LogisticRegression(C=c, class_weight="balanced", max_iter=1500, random_state=SEED)
        model.fit(x[train_mask], y[train_mask], sample_weight=weight)
        prediction = model.predict(x[val_mask])
        score = f1_score(y[val_mask], prediction, average="macro", zero_division=0)
        candidates.append((score, c, model))
    best_score, best_c, room_model = max(candidates, key=lambda item: (item[0], -item[1]))
    room_classes = room_model.classes_.tolist()
    report = {
        "trained_at": timestamp(), "caution": CAUTION,
        "dataset_sha256": manifest["dataset_sha256"],
        "backbone": read_json(ARTIFACTS / "backbone.json"),
        "training_method": "Frozen normalized SigLIP2 embeddings; class-balanced logistic "
        "classification heads; inverse-group-size sample weights. C selected on validation "
        "only for room head; no test tuning.",
        "room": {
            "classes": room_classes, "chosen_C": best_c,
            "validation_macro_f1": float(best_score),
            "validation": metrics(y[val_mask], room_model.predict(x[val_mask]), room_classes),
            "test": metrics(y[test_mask], room_model.predict(x[test_mask]), room_classes),
            "majority_baseline_test_accuracy": float(np.mean(
                y[test_mask] == Counter(y[train_mask]).most_common(1)[0][0]
            )),
            "train_images": int(train_mask.sum()),
        },
        "features": {}, "skipped_features": {},
        "human_reviewed_labels": 0, "production_ready": False,
        "investment_success_head": "Not trained; no independently verified outcome labels",
        "target_fit_head": "Not trained; no approved target/non-target labels",
        "explanations": "Structured predictions and nearest training examples, not generated narratives",
        "evaluation_limitations": [
            "Test targets are earlier model suggestions, not independent ground truth.",
            "Scores cannot establish superiority to gpt-4.1-mini, which supplied the labels.",
            "Rooms/features may inherit teacher errors; machine captions can be misassigned.",
            "Random property-disjoint split is not a later-period or cross-market generalization test.",
            "Near-duplicate screening is conservative, not proof all visual leakage is absent.",
            "Virtual staging missed by the draft annotations may remain.",
            "Small rare classes and sparse negatives limit feature-head reliability.",
        ],
    }
    heads = {}
    for name in FEATURES:
        known = np.asarray([name in row["feature_labels"] for row in rows])
        ft_train = known & train_mask
        ft_val = known & val_mask
        ft_test = known & test_mask
        labels = np.asarray([row["feature_labels"].get(name, -1) for row in rows])
        counts = Counter(labels[ft_train].tolist())
        positive_groups = {row["group_id"] for row in rows
                           if row["split"] == "train" and row["feature_labels"].get(name) == 1}
        negative_groups = {row["group_id"] for row in rows
                           if row["split"] == "train" and row["feature_labels"].get(name) == 0}
        if min(counts.get(0, 0), counts.get(1, 0)) < 12 or min(
            len(positive_groups), len(negative_groups)
        ) < 5:
            report["skipped_features"][name] = {
                "reason": "Insufficient reviewed/draft positive and negative diversity for a baseline",
                "train_label_counts": dict(counts), "positive_groups": len(positive_groups),
                "negative_groups": len(negative_groups),
            }
            continue
        head = LogisticRegression(C=1.0, class_weight="balanced", max_iter=1500, random_state=SEED)
        selected_rows = [row for row, selected in zip(rows, ft_train) if selected]
        head.fit(x[ft_train], labels[ft_train], sample_weight=group_weights(selected_rows))
        heads[name] = head
        entry = {
            "train_known": int(ft_train.sum()), "validation_known": int(ft_val.sum()),
            "test_known": int(ft_test.sum()), "unknown_labels_excluded": int((~known).sum()),
            "train_label_counts": dict(counts),
            "threshold": .5, "threshold_calibrated": False,
            "test": metrics(labels[ft_test], head.predict(x[ft_test]), [0, 1])
            if ft_test.any() else None,
            "majority_baseline_test_accuracy": float(np.mean(
                labels[ft_test] == counts.most_common(1)[0][0]
            )) if ft_test.any() else None,
            "candidate_only": True,
        }
        report["features"][name] = entry
    bundle = {
        "room_model": room_model, "feature_models": heads,
        "dataset_sha256": manifest["dataset_sha256"],
        "backbone_revision": embedded["backbone_revision"].item(),
        "checkpoint": CHECKPOINT, "caution": CAUTION, "artifact_version": 1,
    }
    ARTIFACTS.mkdir(exist_ok=True)
    joblib.dump(bundle, ARTIFACTS / "silver_heads.joblib")
    write_json(ARTIFACTS / "metrics.json", report)
    write_json(ARTIFACTS / "label_dictionary.json", {
        "rooms": room_classes, "trained_features": sorted(heads),
        "unknown_features": "Not predicted or no training label; never map to absence",
    })
    # An independent reload/predict verifies persistence, not just an in-memory fit.
    restored = joblib.load(ARTIFACTS / "silver_heads.joblib")
    np.testing.assert_array_equal(
        restored["room_model"].predict(x[test_mask]), room_model.predict(x[test_mask]),
    )
    errors = []
    for index in np.flatnonzero(test_mask):
        predicted = room_model.predict(x[index:index+1])[0]
        if predicted != rows[index]["room_label"]:
            errors.append({
                "image_id": rows[index]["image_id"], "image_path": rows[index]["image_path"],
                "draft_room": rows[index]["room_label"], "model_room": predicted,
                "review_status": "unreviewed_holdout_disagreement",
                "do_not_train_on_test": True,
            })
    write_json(ARTIFACTS / "holdout_disagreements.json", errors)
    print(json.dumps({
        "room_test_agreement": report["room"]["test"]["accuracy_vs_draft_labels"],
        "room_validation_macro_f1": best_score, "feature_heads": len(heads),
        "skipped_features": list(report["skipped_features"]), "caution": CAUTION,
    }, indent=2), flush=True)


def predict(image_path: Path, output: Path | None) -> None:
    import joblib
    import numpy as np
    from PIL import Image, ImageOps
    from transformers import AutoImageProcessor, SiglipVisionModel

    torch = torch_setup()
    from studio_worker import load_starting_bundle
    bundle, model_version = load_starting_bundle()
    backbone = read_json(ARTIFACTS / "backbone.json")
    if backbone["revision"] != bundle["backbone_revision"]:
        raise ValueError("Prediction backbone and heads are incompatible")
    model = SiglipVisionModel.from_pretrained(backbone["snapshot_path"], local_files_only=True).eval()
    processor = AutoImageProcessor.from_pretrained(
        backbone["snapshot_path"], local_files_only=True, use_fast=False,
    )
    with Image.open(image_path) as source:
        image = ImageOps.exif_transpose(source).convert("RGB")
    with torch.inference_mode():
        vector = model(**processor(images=[image], return_tensors="pt")).pooler_output
        vector = torch.nn.functional.normalize(vector.float(), dim=-1).cpu().numpy()
    room = bundle["room_model"]
    probabilities = room.predict_proba(vector)[0]
    manifest = read_json(DATA / "manifest.json")
    training = [row for row in manifest["images"] if row["split"] == "train"]
    embeddings = np.load(DATA / "embeddings.npz", allow_pickle=False)
    index_by = {key: index for index, key in enumerate(embeddings["image_ids"].tolist())}
    predicted_room = str(room.classes_[int(probabilities.argmax())])
    nearby = [row for row in training if row["room_label"] == predicted_room]
    query_hash = sha(image_path)
    scores = [
        (float(embeddings["embeddings"][index_by[row["image_id"]]] @ vector[0]), row)
        for row in nearby if row["sha256"] != query_hash
    ]
    scores.sort(key=lambda value: value[0], reverse=True)
    result = {
        "model_version": model_version,
        "image_path": str(image_path.resolve()), "image_sha256": sha(image_path),
        "room": predicted_room, "room_scores_uncalibrated": dict(zip(
            room.classes_.tolist(), [float(value) for value in probabilities],
        )),
        "feature_scores_uncalibrated": {
            key: float(model.predict_proba(vector)[0, list(model.classes_).index(1)])
            if key not in FEATURE_ROOMS or predicted_room in FEATURE_ROOMS[key] else None
            for key, model in bundle["feature_models"].items()
        },
        "feature_scope_note": "Room-specific heads are omitted outside their proposed room scope. "
        "A null score is not absence. Scores do not prove the feature or surface is visible. "
        "This baseline has no trained feature-visibility gate; no features are automatically approved. "
        "Room predictions and scores still need human review.",
        "feature_visibility_verified": False,
        "nearest_training_examples": [
            {"image_id": row["image_id"], "image_path": row["image_path"],
             "draft_room": row["room_label"], "cosine_similarity": score}
            for score, row in scores[:5]
        ],
        "investment_success": None, "target_fit": None,
        "caution": CAUTION, "requires_human_review": True,
    }
    if output:
        write_json(output, result)
    print(json.dumps(result, indent=2))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("prepare")
    embedding = commands.add_parser("embed")
    embedding.add_argument("--batch-size", type=int, default=8)
    embedding.add_argument("--limit", type=int)
    commands.add_parser("train")
    prediction = commands.add_parser("predict")
    prediction.add_argument("--image", type=Path, required=True)
    prediction.add_argument("--output", type=Path)
    args = parser.parse_args()
    status_path = ROOT / "training_status.json"
    status = {"stage": args.command, "status": "running", "started_at": timestamp(),
              "caution": CAUTION, "workspace": str(ROOT)}
    write_json(status_path, status)
    try:
        if args.command == "prepare":
            prepare()
        elif args.command == "embed":
            if args.batch_size < 1 or (args.limit is not None and args.limit < 1):
                raise ValueError("Batch size and limit must be positive")
            embed(args.batch_size, args.limit)
        elif args.command == "train":
            train()
        else:
            predict(args.image, args.output)
    except Exception as exc:
        status.update(status="failed", finished_at=timestamp(), error_type=type(exc).__name__,
                      error=str(exc))
        write_json(status_path, status)
        raise
    status.update(status="completed", finished_at=timestamp())
    write_json(status_path, status)


if __name__ == "__main__":
    main()


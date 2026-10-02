"""Train versioned V1 metadata, vision, and fusion candidates from a frozen dataset."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

import joblib
import numpy as np

import pilot
from model_workbench import pending_training
from studio_data import now
from v1_models import (
    FEATURE_POLICY, FusionClassifier, MetadataClassifier, VisionClassifier,
    classification_metrics, require_class_diversity,
)


def property_rows(store,scorer,dataset):
    rows = []
    for item in dataset["examples"]:
        detail = store.property(item["property_id"])
        excluded = set(item.get("excluded_photo_ids",[]))
        images = [
            image for image in detail["images"]
            if image["id"] not in excluded
            and image.get("selection",{}).get("included",True)
            and image.get("effective",{}).get("context")
                not in {"shared_amenity","floor_plan","unrelated"}
        ][:12]
        vectors = scorer.embeddings([store.image_path(image["id"]) for image in images])
        rows.append({
            **item,
            "metadata":detail["property"].get("metadata",{}),
            "remarks":detail["property"].get("mls_remarks") or "",
            "vectors":vectors,
            "image_count":len(vectors),
            "coverage":detail.get("historical_source",{}).get("photo_coverage","unknown"),
        })
    return rows


def labels(rows):
    return np.asarray([int(row["target_label"]=="TARGET") for row in rows])


def train_candidate(rows):
    train = [row for row in rows if row["split"]=="train"]
    validation = [row for row in rows if row["split"]=="validation"]
    test = [row for row in rows if row["split"]=="test"]
    require_class_diversity(labels(train),[row["group_id"] for row in train])
    metadata = MetadataClassifier.fit(
        [row["metadata"] for row in train],[row["remarks"] for row in train],
        labels(train),
    )
    vision_train = [row for row in train if row["vectors"]]
    require_class_diversity(
        labels(vision_train),[row["group_id"] for row in vision_train],
        minimum_per_class=3,minimum_groups=3,
    )
    # Choose aggregation using validation only; protected test is not inspected.
    vision_options = {}
    for mode in ("mean","max","mean_max"):
        model = VisionClassifier.fit(
            [row["vectors"] for row in vision_train],labels(vision_train),mode,
        )
        usable = [row for row in validation if row["vectors"]]
        metrics = classification_metrics(labels(usable),model.predict(
            [row["vectors"] for row in usable]
        )) if usable else {"n":0,"balanced_accuracy":0}
        vision_options[mode] = (model,metrics)
    selected_mode = max(
        vision_options,key=lambda mode:(
            vision_options[mode][1].get("balanced_accuracy") or 0,mode
        )
    )
    vision = vision_options[selected_mode][0]

    validation_meta = metadata.predict(
        [row["metadata"] for row in validation],[row["remarks"] for row in validation]
    )
    validation_vision = vision.predict([row["vectors"] for row in validation])
    require_class_diversity(
        labels(validation),[row["group_id"] for row in validation],
        minimum_per_class=2,minimum_groups=2,
    )
    fusion = FusionClassifier.fit(
        validation_meta,validation_vision,
        [row["image_count"] for row in validation],labels(validation),
    )

    def evaluate(items):
        if not items:
            return {}
        meta = metadata.predict(
            [row["metadata"] for row in items],[row["remarks"] for row in items]
        )
        visual = vision.predict([row["vectors"] for row in items])
        combined = fusion.predict(meta,visual,[row["image_count"] for row in items])
        y = labels(items)
        slices = {}
        for name,predicate in {
            "metadata_only":lambda row:row["image_count"]==0,
            "limited_visual":lambda row:0<row["image_count"]<=2,
            "strong_visual":lambda row:row["image_count"]>=3,
            "hard_negative":lambda row:row.get("hard_negative") is True,
        }.items():
            indices = [i for i,row in enumerate(items) if predicate(row)]
            slices[name] = classification_metrics(y[indices],combined[indices]) if indices else {"n":0}
        return {
            "metadata":classification_metrics(y,meta),
            "vision":classification_metrics(y,visual),
            "fusion":classification_metrics(y,combined),
            "slices":slices,
            "predictions":[{
                "property_id":row["property_id"],"target_label":row["target_label"],
                "metadata_score":float(meta[i]),"vision_score":(
                    float(visual[i]) if np.isfinite(visual[i]) else None
                ),"fusion_score":float(combined[i]),
                "mode_used":"metadata_only" if row["image_count"]==0 else
                    "fusion_limited_visual" if row["image_count"]<=2 else "fusion",
            } for i,row in enumerate(items)],
        }

    metrics = {
        "objective":"target-vs-not-target-v1",
        "feature_policy":FEATURE_POLICY,
        "counts":{
            "train":len(train),"validation":len(validation),"test":len(test),
            "targets":sum(row["target_label"]=="TARGET" for row in rows),
            "not_targets":sum(row["target_label"]=="NOT_TARGET" for row in rows),
            "hard_negatives":sum(row.get("hard_negative") is True for row in rows),
        },
        "vision_aggregation":selected_mode,
        "vision_validation_options":{
            mode:result[1] for mode,result in vision_options.items()
        },
        "protected_test":evaluate(test),
        "condition_labels":dict(Counter(row.get("physical_condition","UNKNOWN") for row in rows)),
        "modernization_labels":dict(Counter(row.get("modernization_state","UNKNOWN") for row in rows)),
        "warnings":[
            "Protected test was evaluated only after model and aggregation selection.",
            "Condition and modernization are reported but not trained until class coverage is sufficient.",
        ],
    }
    return {
        "metadata_model":metadata,"vision_model":vision,"fusion_model":fusion,
        "feature_policy":FEATURE_POLICY,"metrics":metrics,
    }


def process_training_request(store,scorer):
    request = pending_training(store)
    if not request:
        return False
    key = "workbench-training:"+request["id"]
    current = store.document(key)
    if not current or current.get("status")!="queued":
        return False
    claimed = store.save_document(
        key,{**current,"status":"running","started_at":now()},current["revision"]
    )
    latest_pointer = store.document("workbench-training-latest") or {}
    store.save_document(
        "workbench-training-latest",
        {**{k:v for k,v in claimed.items() if k!="revision"}},
        latest_pointer.get("revision",0),
    )
    try:
        dataset = store.document("workbench-dataset:"+claimed["dataset_fingerprint"])
        if not dataset or dataset.get("status")!="frozen":
            raise ValueError("Frozen dataset is unavailable")
        rows = property_rows(store,scorer,dataset)
        bundle = train_candidate(rows)
        folder = pilot.ARTIFACTS/"workbench_candidates"/claimed["id"]
        folder.mkdir(parents=True,exist_ok=False)
        joblib.dump(bundle,folder/"candidate.joblib")
        pilot.write_json(folder/"dataset.json",dataset)
        pilot.write_json(folder/"metrics.json",bundle["metrics"])
        artifact_sha = pilot.sha(folder/"candidate.joblib")
        result = {
            "id":claimed["id"],"status":"completed","completed_at":now(),
            "version":claimed["id"],"dataset_version":dataset["dataset_version"],
            "dataset_fingerprint":dataset["fingerprint"],
            "artifact_path":str(folder/"candidate.joblib"),
            "artifact_sha256":artifact_sha,"metrics":bundle["metrics"],
            "policy":"workbench-candidate-v1",
        }
        store.save_document("workbench-candidate:"+claimed["id"],result,0)
        candidate_latest = store.document("workbench-candidate-latest") or {}
        store.save_document(
            "workbench-candidate-latest",result,candidate_latest.get("revision",0)
        )
        state = store.document(key)
        store.save_document(
            key,{**{k:v for k,v in state.items() if k!="revision"},**result},
            state["revision"],
        )
        training_latest = store.document("workbench-training-latest") or {}
        store.save_document(
            "workbench-training-latest",result,training_latest.get("revision",0)
        )
    except Exception as error:
        state = store.document(key)
        failure = {
            **{k:v for k,v in state.items() if k!="revision"},
            "status":"failed","completed_at":now(),
            "error":str(error)[:400],
        }
        store.save_document(key,failure,state["revision"])
        training_latest = store.document("workbench-training-latest") or {}
        store.save_document(
            "workbench-training-latest",failure,training_latest.get("revision",0)
        )
    return True

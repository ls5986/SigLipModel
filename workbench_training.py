"""Train versioned V1 metadata, vision, and fusion candidates from a frozen dataset."""
from __future__ import annotations

import hashlib
import json
from collections import Counter
from pathlib import Path

import joblib
import numpy as np
from sklearn.model_selection import StratifiedGroupKFold

import pilot
from model_workbench import pending_training
from studio_data import now
from v1_models import (
    FEATURE_POLICY, FusionClassifier, MetadataClassifier, VisionClassifier,
    acquisition_time_metadata,
    classification_metrics, require_class_diversity,
)


def property_rows(store,scorer,dataset):
    rows = []
    for item in dataset["examples"]:
        excluded = set(item.get("excluded_photo_ids",[]))
        if item["property_id"].startswith("challenge:"):
            from mls_source import ExistingMLSSupabaseSource
            from model_workbench import challenge_item
            challenge = challenge_item(store,item["property_id"])
            if not hasattr(scorer,"mls_source"):
                scorer.mls_source = ExistingMLSSupabaseSource.from_env()
            blobs = []
            for photo in challenge.get("images",[])[:12]:
                image_id = item["property_id"]+":"+photo["media_key"]
                if image_id in excluded:
                    continue
                blob = scorer.mls_source.image_bytes(
                    challenge["listing_key"],photo["media_key"],
                )
                if hashlib.sha256(blob).hexdigest()!=photo.get("sha256"):
                    raise ValueError(
                        "MLS photo changed after the challenge batch was frozen"
                    )
                blobs.append(blob)
            vectors = scorer.embedding_blobs(blobs) if blobs else []
            metadata = challenge.get("metadata",{})
            metadata = acquisition_time_metadata(metadata)
            remarks = metadata.get("PublicRemarks") or ""
            coverage = "selected_mls_media" if blobs else "metadata_only"
        else:
            detail = store.property(item["property_id"])
            if item.get("origin") == "training-studio-v2":
                from studio_v2 import label_evidence
                prop = detail["property"]
                if item.get("label_evidence_id") != label_evidence(
                    prop["id"], prop.get("mls_remarks") or "", detail["images"], prop.get("metadata")
                ):
                    raise ValueError("Reviewed evidence changed after dataset freeze; training is blocked")
            images = [
                image for image in detail["images"]
                if image["id"] not in excluded
                and image.get("selection",{}).get("included",True)
                and image.get("effective",{}).get("context")
                    not in {"shared_amenity","floor_plan","unrelated"}
            ][:12]
            vectors = scorer.embeddings([
                store.image_path(image["id"]) for image in images
            ])
            metadata = detail["property"].get("metadata",{})
            metadata = acquisition_time_metadata(
                metadata,(detail.get("historical_source") or {}).get("source"),
            )
            remarks = detail["property"].get("mls_remarks") or ""
            coverage = detail.get(
                "historical_source",{},
            ).get("photo_coverage","unknown")
        rows.append({
            **item,
            "metadata":metadata,
            "remarks":remarks,
            "vectors":vectors,
            "image_count":len(vectors),
            "coverage":coverage,
        })
    return rows


def labels(rows):
    if any(row.get("target_label") not in {"TARGET", "NOT_TARGET"} for row in rows):
        raise ValueError("UNKNOWN acquisition fit cannot become a negative training label")
    return np.asarray([int(row["target_label"]=="TARGET") for row in rows])


def grouped_folds(rows,maximum=5):
    y = labels(rows)
    groups = np.asarray([row["group_id"] for row in rows])
    group_counts = {
        label:len({groups[i] for i,value in enumerate(y) if value==label})
        for label in (0,1)
    }
    folds = min(maximum,min(group_counts.values()))
    if folds<2:
        raise ValueError("Need at least two independent groups in each class for OOF stacking")
    splitter = StratifiedGroupKFold(
        n_splits=folds,shuffle=True,random_state=20260922,
    )
    result = []
    for fit,holdout in splitter.split(np.zeros(len(rows)),y,groups):
        if set(groups[fit]) & set(groups[holdout]):
            raise AssertionError("Physical property group crossed an OOF fold")
        result.append((fit,holdout))
    return result,{"folds":folds,"groups_per_class":group_counts,
                   "policy":"stratified-group-kfold-seed-20260922"}


def metadata_oof(rows,folds,*,text_encoder=None):
    scores = np.full(len(rows),np.nan)
    for fit,holdout in folds:
        model = MetadataClassifier.fit(
            [rows[i]["metadata"] for i in fit],
            [rows[i]["remarks"] for i in fit],
            labels([rows[i] for i in fit]), text_encoder=text_encoder,
        )
        scores[holdout] = model.predict(
            [rows[i]["metadata"] for i in holdout],
            [rows[i]["remarks"] for i in holdout],
        )
    if not np.isfinite(scores).all():
        raise AssertionError("Metadata OOF predictions are incomplete")
    return scores


def vision_oof(rows,folds,aggregation):
    scores = np.full(len(rows),np.nan)
    for fit,holdout in folds:
        fit_rows = [rows[i] for i in fit if rows[i]["vectors"]]
        model = VisionClassifier.fit(
            [row["vectors"] for row in fit_rows],labels(fit_rows),aggregation,
        )
        holdout_rows = [rows[i] for i in holdout]
        scores[holdout] = model.predict([row["vectors"] for row in holdout_rows])
    return scores


def train_candidate(rows, progress=lambda stage: None, *, text_encoder=None):
    train = [row for row in rows if row["split"]=="train"]
    validation = [row for row in rows if row["split"]=="validation"]
    test = [row for row in rows if row["split"]=="test"]
    require_class_diversity(labels(train),[row["group_id"] for row in train])
    require_class_diversity(
        labels(validation),[row["group_id"] for row in validation],
        minimum_per_class=2,minimum_groups=2,
    )
    require_class_diversity(
        labels(test),[row["group_id"] for row in test],
        minimum_per_class=1,minimum_groups=1,
    )
    vision_train = [row for row in train if row["vectors"]]
    require_class_diversity(
        labels(vision_train),[row["group_id"] for row in vision_train],
        minimum_per_class=3,minimum_groups=3,
    )
    # Choose aggregation using validation only; protected test is not inspected.
    progress("selecting_vision_aggregation")
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
    progress("generating_grouped_oof_predictions")
    folds,fold_report = grouped_folds(train)
    oof_metadata = metadata_oof(train,folds,text_encoder=text_encoder)
    oof_vision = vision_oof(train,folds,selected_mode)
    progress("training_fusion_from_oof")
    fusion = FusionClassifier.fit(
        oof_metadata,oof_vision,[row["image_count"] for row in train],labels(train),
    )
    metadata = MetadataClassifier.fit(
        [row["metadata"] for row in train],[row["remarks"] for row in train],
        labels(train), text_encoder=text_encoder,
    )
    vision = VisionClassifier.fit(
        [row["vectors"] for row in vision_train],labels(vision_train),selected_mode,
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

    progress("evaluating_protected_test")
    metrics = {
        "objective":"target-vs-not-target-v1",
        "feature_policy":FEATURE_POLICY,
        "text_features": {"schema_version":getattr(metadata.text,"feature_schema_version","actvision-text-tfidf-v2"),
                          "checkpoint_sha256":getattr(metadata.text,"checkpoint_sha256",None)},
        "counts":{
            "train":len(train),"validation":len(validation),"test":len(test),
            "targets":sum(row["target_label"]=="TARGET" for row in rows),
            "not_targets":sum(row["target_label"]=="NOT_TARGET" for row in rows),
            "hard_negatives":sum(row.get("hard_negative") is True for row in rows),
        },
        "vision_aggregation":selected_mode,
        "fusion_training":{
            "source":"grouped_oof_training_predictions",
            **fold_report,
            "validation_labels_used":False,
            "protected_test_used":False,
        },
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
        "feature_policy":FEATURE_POLICY,
        "text_features": {"schema_version":getattr(metadata.text,"feature_schema_version","actvision-text-tfidf-v2"),
                          "checkpoint_sha256":getattr(metadata.text,"checkpoint_sha256",None)},"metrics":metrics,
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
        key,{**current,"status":"running","stage":"loading_frozen_dataset",
             "started_at":now()},current["revision"]
    )
    latest_pointer = store.document("workbench-training-latest") or {}
    store.save_document(
        "workbench-training-latest",
        {**{k:v for k,v in claimed.items() if k!="revision"}},
        latest_pointer.get("revision",0),
    )
    def progress(stage):
        state = store.document(key)
        updated = {
            **{k:v for k,v in state.items() if k!="revision"},
            "status":"running","stage":stage,"stage_updated_at":now(),
        }
        store.save_document(key,updated,state["revision"])
        latest = store.document("workbench-training-latest") or {}
        store.save_document(
            "workbench-training-latest",updated,latest.get("revision",0),
        )
    try:
        dataset = store.document("workbench-dataset:"+claimed["dataset_fingerprint"])
        if not dataset or dataset.get("status")!="frozen":
            raise ValueError("Frozen dataset is unavailable")
        progress("extracting_metadata_and_embeddings")
        rows = property_rows(store,scorer,dataset)
        bundle = train_candidate(rows,progress)
        progress("writing_versioned_artifact")
        folder = pilot.ARTIFACTS/"workbench_candidates"/claimed["id"]
        folder.mkdir(parents=True,exist_ok=False)
        joblib.dump(bundle,folder/"candidate.joblib")
        pilot.write_json(folder/"dataset.json",dataset)
        pilot.write_json(folder/"metrics.json",bundle["metrics"])
        artifact_sha = pilot.sha(folder/"candidate.joblib")
        result = {
            "id":claimed["id"],"status":"completed","completed_at":now(),
            "stage":"completed",
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


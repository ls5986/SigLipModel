"""Grouped ActVision v2 training orchestration and immutable candidate artifacts."""
from __future__ import annotations

from collections import Counter
from copy import deepcopy
from io import BytesIO
import hashlib
import os
from importlib.metadata import version as package_version
from uuid import uuid4

import numpy as np
from sklearn.metrics import (
    balanced_accuracy_score, f1_score, roc_auc_score,
)
from sklearn.model_selection import GroupKFold

from actvision_contract import canonical_json, digest, unknown_result, validate_contract
from fusion_model import FusionModel
from property_models import VisionEvidenceModel
from structured_model import StructuredModel, completeness
from text_model import TextModel
from v2_calibration import CalibratedModel, TemperatureCalibrator, expected_calibration_error
from v2_dataset import AXES, load_frozen
from v2_training_io import artifact_preflight, embed_rows, observe_training, safe_error, stage
from studio_data import now

REQUEST_KEY = "actvision-v2-training-current"
WORKER_KEY = "actvision-v2-model-worker"
COMPONENTS = ("vision", "text", "structured", "fusion")
MIN_TRAIN_GROUPS = 20


def _labels(rows):
    return [row["labels"] for row in rows]


def _coverage(row):
    return {
        "usable_photo_count": len(row.get("photos") or []),
        "text_available": bool((row.get("remarks") or "").strip()),
        "structured_completeness": completeness(row.get("structured") or {}),
    }


def _component(name, result, version, calibration_version, *, available=True, reason=None):
    if not available:
        return {
            "status": "missing" if reason and reason.startswith("No ") else "unavailable",
            "version": None, "calibration_version": None,
            "result": unknown_result(), "reason": reason or f"No {name} prediction",
        }
    return {
        "status": "available", "version": version,
        "calibration_version": calibration_version,
        "result": result, "reason": None,
    }


def _fit_component(name, rows, siglip, semantic_encoder):
    labels = _labels(rows)
    if name == "vision":
        bags = [_vision_bag(row, siglip) for row in rows]
        return VisionEvidenceModel.fit(bags, labels), bags
    if name == "text":
        remarks = [row.get("remarks") or "" for row in rows]
        return TextModel.fit(remarks, labels, encoder=semantic_encoder), remarks
    if name == "structured":
        metadata = [row.get("structured") or {} for row in rows]
        return StructuredModel.fit(metadata, labels), metadata
    raise ValueError("Unknown component")


def _predict_component(name, model, rows, siglip):
    if name == "vision":
        return model.predict([_vision_bag(row, siglip) for row in rows])
    if name == "text":
        return model.predict([row.get("remarks") or "" for row in rows])
    if name == "structured":
        return model.predict([row.get("structured") or {} for row in rows])
    raise ValueError("Unknown component")


def _embed_rows(rows, siglip):
    """Keep photo paths alive only for their bounded encoder batch."""
    return embed_rows(rows, siglip)


def _vision_bag(row, siglip):
    vectors = siglip._actvision_embeddings
    return [
        {"vector": vectors[photo["sha256"]], "room": photo.get("room") or "other"}
        for photo in row.get("photos") or [] if photo["sha256"] in vectors
    ]


def _oof_components(train_rows, siglip, semantic_encoder):
    groups = np.asarray([row["split_group_id"] for row in train_rows])
    unique = sorted(set(groups))
    if len(unique) < MIN_TRAIN_GROUPS:
        raise ValueError("Need at least 20 independent train groups for ActVision v2")
    folds = min(5, len(unique))
    splitter = GroupKFold(n_splits=folds)
    raw = {
        name: [unknown_result() for _ in train_rows]
        for name in ("vision", "text", "structured")
    }
    available = {name: [False] * len(train_rows) for name in raw}
    indices = np.arange(len(train_rows))
    for fold, (fit_index, held_index) in enumerate(splitter.split(indices, groups=groups), 1):
        fit_rows = [train_rows[i] for i in fit_index]
        held_rows = [train_rows[i] for i in held_index]
        for name in raw:
            stage("grouped_oof_" + name, fold=fold, total_folds=folds)
            try:
                model, _ = _fit_component(name, fit_rows, siglip, semantic_encoder)
                predictions = _predict_component(name, model, held_rows, siglip)
            except ValueError:
                continue
            for index, result in zip(held_index, predictions):
                raw[name][int(index)] = result
                available[name][int(index)] = result != unknown_result()
    return raw, available


def _calibrate_components(raw_oof, train_rows, identity):
    labels = _labels(train_rows)
    calibrators = {}
    calibrated = {}
    for name, results in raw_oof.items():
        calibrator = TemperatureCalibrator.fit(
            results, labels, identity=digest({"component": name, "dataset": identity})
        )
        calibrators[name] = calibrator
        calibrated[name] = [calibrator.apply(result) for result in results]
    return calibrators, calibrated


def _fusion_inputs(rows, calibrated, available, calibrators):
    values = []
    coverage = []
    for index, row in enumerate(rows):
        components = {}
        for name in ("vision", "text", "structured"):
            result = calibrated[name][index]
            present = {
                "vision": bool(row.get("photos")),
                "text": bool((row.get("remarks") or "").strip()),
                "structured": bool(row.get("structured")),
            }[name]
            if not present:
                components[name] = _component(
                    name, unknown_result(), "", "", available=False, reason=f"No {name} evidence"
                )
            elif not available[name][index] or result == unknown_result():
                components[name] = _component(
                    name, unknown_result(), "", "", available=False,
                    reason=f"No supported OOF {name} head for this fold",
                )
            else:
                components[name] = _component(
                    name, result, f"oof-{name}", calibrators[name].version
                )
        components["fusion"] = _component(
            "fusion", unknown_result(), "", "", available=False,
            reason="Fusion input placeholder",
        )
        values.append(components)
        coverage.append(_coverage(row))
    return values, coverage


def _fit_final_components(train_rows, siglip, semantic_encoder, calibrators):
    """Fit every supported modality without making one sparse modality fatal."""
    models = {}
    unavailable = {}
    for name in ("vision", "text", "structured"):
        stage("fit_final_" + name)
        try:
            model, _ = _fit_component(name, train_rows, siglip, semantic_encoder)
        except ValueError as exc:
            unavailable[name] = type(exc).__name__
            continue
        models[name] = CalibratedModel.wrap(model, calibrators[name])
    if not models:
        raise ValueError("No ActVision v2 component has sufficient supervised class support")
    return models, unavailable


def _predict_final_components(models, rows, siglip):
    results = {
        name: [unknown_result() for _ in rows]
        for name in ("vision", "text", "structured")
    }
    available = {
        name: [False for _ in rows]
        for name in ("vision", "text", "structured")
    }
    for name, model in models.items():
        predictions = _predict_component(name, model.model, rows, siglip)
        predictions = [model.calibrator.apply(value) for value in predictions]
        results[name] = predictions
        available[name] = [value != unknown_result() for value in predictions]
    return results, available


def _metrics(results, rows, origin=None):
    labels = _labels(rows)
    output = {}
    selected_indices = list(range(len(rows)))
    if origin:
        selected_indices = [
            i for i, row in enumerate(rows)
            if any(source.get("origin") == origin for source in row.get("provenance", {}).values())
        ]
    for task, probability_key in {
        "physical_condition": "condition_probabilities",
        "modernization": "modernization_probabilities",
        "acquisition_fit": "acquisition_fit_probabilities",
    }.items():
        pairs = []
        probabilities = []
        for i in selected_indices:
            truth = labels[i].get(task, "UNKNOWN")
            if truth == "UNKNOWN":
                continue
            result = results[i]
            if result.get(task, "UNKNOWN") == "UNKNOWN":
                continue
            pairs.append((truth, result[task]))
            probabilities.append(result.get(probability_key) or {})
        if not pairs:
            output[task] = {"n": 0}
            continue
        truth = [a for a, _ in pairs]
        pred = [b for _, b in pairs]
        entry = {
            "n": len(pairs),
            "macro_f1": float(f1_score(truth, pred, average="macro", zero_division=0)),
            "balanced_accuracy": float(balanced_accuracy_score(truth, pred)),
        }
        if task == "acquisition_fit" and set(truth) <= {"TARGET", "NOT_TARGET"} and len(set(truth)) == 2:
            scores = [p.get("TARGET") for p in probabilities]
            if all(score is not None for score in scores):
                entry["auroc"] = float(roc_auc_score([value == "TARGET" for value in truth], scores))
        entry["ece"] = expected_calibration_error(
            [results[i] for i in selected_indices], [labels[i] for i in selected_indices], task
        )
        output[task] = entry
    return output


def _artifact_bytes(value):
    import joblib
    buffer = BytesIO()
    joblib.dump(value, buffer, compress=3)
    return buffer.getvalue()


def _upload_component(store, release_id, name, value):
    stage("saving_" + name + "_artifact")
    blob = _artifact_bytes(value)
    key = f"models/actvision-v2/{release_id}/{name}.joblib"
    stored = store.storage.put("acq-training-private", key, blob)
    return {
        "uri": key,
        "sha256": stored["sha256"],
        "framework": "scikit-learn",
        "framework_version": package_version("scikit-learn"),
        "component_version": f"{release_id}:{name}",
        "feature_schema_version": value.feature_schema_version,
        "label_schema_version": "actvision-labels-v2",
        "calibration_version": value.calibrator.version,
    }


def _finish_run(store, run_id, artifact, metrics):
    from psycopg.types.json import Jsonb
    with store.database.connect() as db:
        db.execute(
            "SELECT acq_training.finish_model_run_v2(%s,%s,'completed',%s,%s,%s,null)",
            (store.workspace, run_id, artifact["uri"], artifact["sha256"], Jsonb(metrics)),
        )


def _fail_run(store, run_id, error_code):
    with store.database.connect() as db:
        row = db.execute(
            "SELECT status FROM acq_training.model_runs WHERE workspace_id=%s AND id=%s",
            (store.workspace, run_id),
        ).fetchone()
        if row and row["status"] == "running":
            db.execute(
                "SELECT acq_training.finish_model_run_v2(%s,%s,'failed',null,null,null,%s)",
                (store.workspace, run_id, error_code),
            )


@observe_training
def train_candidate(store, request, siglip):
    frozen = load_frozen(store, request["dataset_id"])
    rows = frozen["rows"]
    train_rows = [row for row in rows if row["split"] == "train"]
    validation_rows = [row for row in rows if row["split"] == "validation"]
    test_rows = [row for row in rows if row["split"] == "test"]
    if len(train_rows) < MIN_TRAIN_GROUPS:
        raise ValueError("Need at least 20 independent v2 train groups")

    artifact_preflight(store, request)
    siglip._actvision_store = store
    siglip._actvision_embeddings = _embed_rows(rows, siglip)

    stage("loading_semantic_encoder")
    from provision_semantic_encoder import verify
    from semantic_text import SemanticTextEncoder
    semantic = verify()
    encoder = SemanticTextEncoder(semantic["directory"], semantic["checkpoint_sha256"])

    raw_oof, oof_available = _oof_components(train_rows, siglip, encoder)
    stage("calibrating_components")
    component_calibrators, calibrated_oof = _calibrate_components(
        raw_oof, train_rows, frozen["dataset"]["manifest_sha256"]
    )
    fusion_components, train_coverage = _fusion_inputs(
        train_rows, calibrated_oof, oof_available, component_calibrators
    )
    stage("fitting_fusion")
    fusion = FusionModel.fit(
        fusion_components, train_coverage, _labels(train_rows),
        prediction_source="grouped_oof_training",
    )

    final_components, unavailable_components = _fit_final_components(
        train_rows, siglip, encoder, component_calibrators
    )
    stage("validating_candidate")
    validation_raw, validation_available = _predict_final_components(
        final_components, validation_rows, siglip
    )
    validation_inputs, validation_coverage = _fusion_inputs(
        validation_rows, validation_raw, validation_available,
        {name: final_components[name].calibrator for name in final_components},
    )
    raw_validation_fusion = fusion.predict(validation_inputs, validation_coverage)
    fusion_calibrator = TemperatureCalibrator.fit(
        raw_validation_fusion, _labels(validation_rows),
        identity=digest({"fusion": frozen["dataset"]["manifest_sha256"]}),
    )
    calibrated_fusion = CalibratedModel.wrap(fusion, fusion_calibrator)

    release_id = str(uuid4())
    artifacts = {name: None for name in COMPONENTS}
    component_metrics = {}
    release_run_ids = {name: request["run_ids"][name] for name in COMPONENTS}
    for name in ("vision", "text", "structured"):
        if name not in final_components:
            component_metrics[name] = {
                "status": "unavailable",
                "reason": "insufficient_class_support",
            }
            _fail_run(store, request["run_ids"][name], "insufficient_class_support")
            release_run_ids[name] = None
            continue
        model = final_components[name]
        artifacts[name] = _upload_component(store, release_id, name, model)
        predictions = validation_raw[name] if validation_rows else []
        component_metrics[name] = {
            "validation": _metrics(predictions, validation_rows) if validation_rows else {},
            "calibration_version": model.calibrator.version,
        }
        _finish_run(store, request["run_ids"][name], artifacts[name], component_metrics[name])

    artifacts["fusion"] = _upload_component(store, release_id, "fusion", calibrated_fusion)
    validation_fused = [fusion_calibrator.apply(value) for value in raw_validation_fusion]
    fusion_metrics = {
        "validation": _metrics(validation_fused, validation_rows),
        "human_validation": _metrics(validation_fused, validation_rows, "HUMAN_APPROVED"),
        "ai_draft_validation": _metrics(validation_fused, validation_rows, "AI_DRAFT"),
        "calibration_version": fusion_calibrator.version,
    }
    _finish_run(store, request["run_ids"]["fusion"], artifacts["fusion"], fusion_metrics)

    # Protected test is touched only after the complete candidate and calibration are fixed.
    stage("protected_evaluation")
    test_components_raw, test_available = _predict_final_components(final_components, test_rows, siglip)
    test_inputs, test_coverage = _fusion_inputs(
        test_rows, test_components_raw, test_available,
        {name: final_components[name].calibrator for name in final_components},
    )
    protected_results = [
        fusion_calibrator.apply(value)
        for value in fusion.predict(test_inputs, test_coverage)
    ] if test_rows else []
    protected = _metrics(protected_results, test_rows) if test_rows else {}
    protected_human = _metrics(protected_results, test_rows, "HUMAN_APPROVED") if test_rows else {}

    human_protected_n = sum(
        any(source.get("origin") == "HUMAN_APPROVED" for source in row.get("provenance", {}).values())
        for row in test_rows
    )
    gates = {
        "minimum_human_protected": human_protected_n >= 50,
        "condition_macro_f1": (protected.get("physical_condition", {}).get("macro_f1") or 0) >= .70,
        "modernization_macro_f1": (protected.get("modernization", {}).get("macro_f1") or 0) >= .70,
        "acquisition_fit_auroc": (protected.get("acquisition_fit", {}).get("auroc") or 0) >= .75,
        "ece": all(
            protected.get(axis, {}).get("n", 0) >= 10
            and protected.get(axis, {}).get("ece") is not None
            and protected[axis]["ece"] <= .10
            for axis in ("physical_condition", "modernization", "acquisition_fit")
        ),
    }
    protected_passed = all(gates.values())

    evaluation = {
        "validation": fusion_metrics["validation"],
        "protected_test": protected,
        "protected_human": protected_human,
        "human_protected_groups": human_protected_n,
        "acceptance_gates": gates,
        "protected_slices_passed": protected_passed,
        "limitations": [] if protected_passed else [
            "Research candidate only: protected release gates are not all satisfied."
        ],
    }
    report_sha = digest(evaluation)
    manifest = {
        "kind": "release", "schema_version": "actvision-v2",
        "label_schema_version": "actvision-labels-v2",
        "release_id": release_id, "status": "candidate",
        "components": artifacts,
        "dataset_sha256": frozen["dataset"]["manifest_sha256"],
        "code_commit": os.environ.get("RENDER_GIT_COMMIT", "unknown"),
        "split_policy_version": "actvision-group-split-v2",
        "backbones": {
            "vision": "google/siglip2-base-patch16-224",
            "text": "sentence-transformers/all-MiniLM-L6-v2",
        },
        "thresholds": {},
        "created_at": now(),
        "evaluation": {
            "report_sha256": report_sha,
            "protected_slices_passed": protected_passed,
        },
        "approved_by": None, "approved_at": None,
    }
    validate_contract(manifest, "release")
    bundle_sha = hashlib.sha256(canonical_json(manifest).encode()).hexdigest()
    stage("saving_candidate_release")
    from psycopg.types.json import Jsonb
    with store.database.connect() as db:
        release_version = db.execute(
            "SELECT coalesce(max(version),0)+1 AS version FROM acq_training.model_releases "
            "WHERE workspace_id=%s AND name='actvision-v2'",
            (store.workspace,),
        ).fetchone()["version"]
        db.execute(
            "SELECT acq_training.create_candidate_release_v2(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
            (
                store.workspace, release_id, "actvision-v2", release_version,
                release_run_ids["vision"], release_run_ids["text"],
                release_run_ids["structured"], release_run_ids["fusion"],
                Jsonb(evaluation), Jsonb(manifest), bundle_sha, fusion_calibrator.version,
            ),
        )
    return {
        "release_id": release_id,
        "release_version": release_version,
        "manifest": manifest,
        "evaluation": evaluation,
        "production_ready": protected_passed,
    }


def enqueue(store, payload, actor):
    if payload.get("confirmed") is not True:
        raise ValueError("Confirm training ActVision v2 on the frozen dataset")
    dataset_id = payload.get("dataset_id") or (store.document("actvision-v2-dataset-latest") or {}).get("id")
    if not dataset_id:
        raise ValueError("Freeze an ActVision v2 dataset first")
    frozen = load_frozen(store, dataset_id)
    train_groups = {row["split_group_id"] for row in frozen["rows"] if row["split"] == "train"}
    if len(train_groups) < MIN_TRAIN_GROUPS:
        raise ValueError("Need at least 20 independent train groups")

    previous = store.document(REQUEST_KEY) or {}
    if previous.get("status") in {"queued", "running"}:
        return public_status(store)
    run_ids = {name: str(uuid4()) for name in COMPONENTS}
    from psycopg.types.json import Jsonb
    with store.database.connect() as db:
        for name, run_id in run_ids.items():
            backbone = "google/siglip2-base-patch16-224" if name == "vision" else (
                "sentence-transformers/all-MiniLM-L6-v2" if name == "text" else "none"
            )
            db.execute(
                "SELECT acq_training.queue_model_run_v2(%s,%s,%s,%s,%s,%s)",
                (store.workspace, run_id, dataset_id, name, backbone,
                 Jsonb({"orchestrator": "actvision-v2-grouped-oof-v1"})),
            )
    request = {
        "id": str(uuid4()), "status": "queued", "dataset_id": dataset_id,
        "dataset_fingerprint": frozen["dataset"]["manifest_sha256"],
        "run_ids": run_ids, "requested_by": actor, "requested_at": now(),
        "policy": "actvision-v2-grouped-oof-v1",
    }
    store.save_document(REQUEST_KEY, request, previous.get("revision", 0))
    return public_status(store)


def public_status(store):
    request = store.document(REQUEST_KEY) or {"status": "none"}
    runs = {}
    if request.get("run_ids"):
        with store.database.connect() as db:
            rows = db.execute(
                "SELECT id,target_task,status,metrics,error_code,created_at,completed_at "
                "FROM acq_training.model_runs WHERE workspace_id=%s AND id=ANY(%s::uuid[])",
                (store.workspace, list(request["run_ids"].values())),
            ).fetchall()
        runs = {row["target_task"]: {
            **{k: row[k] for k in ("status", "metrics", "error_code")},
            "created_at": str(row["created_at"]),
            "completed_at": str(row["completed_at"]) if row["completed_at"] else None,
        } for row in rows}
    release = None
    if request.get("release_id"):
        with store.database.connect() as db:
            row = db.execute(
                "SELECT id,version,status,evaluation_summary,bundle_manifest,created_at "
                "FROM acq_training.model_releases WHERE workspace_id=%s AND id=%s",
                (store.workspace, request["release_id"]),
            ).fetchone()
        if row:
            release = {**dict(row), "id": str(row["id"]), "created_at": str(row["created_at"])}
    progress = store.document("actvision-v2-training-progress:" + str(request["id"])) if request.get("id") else None
    return {"request": request, "runs": runs, "release": release, "progress": progress}


def heartbeat(store, status, *, detail=None):
    previous = store.document(WORKER_KEY) or {}
    payload = {
        "status": status, "at": now(), "detail": detail,
        "policy": "actvision-v2-grouped-oof-v1",
        "deployed_commit": os.environ.get("RENDER_GIT_COMMIT", "unknown")[:40],
    }
    return store.save_document(WORKER_KEY, payload, previous.get("revision", 0))


def poll_training(store, siglip):
    request = store.document(REQUEST_KEY) or {}
    if request.get("status") not in {"queued", "running"}:
        return False
    if request["status"] == "running":
        # Training is not automatically replayed after a worker crash because a
        # partial artifact set must be inspected before a new request.
        return False
    claimed = []
    try:
        with store.database.connect() as db:
            for name in COMPONENTS:
                run_id = request["run_ids"][name]
                ok = db.execute(
                    "SELECT acq_training.claim_model_run_v2(%s,%s) AS claimed",
                    (store.workspace, run_id),
                ).fetchone()["claimed"]
                if not ok:
                    raise RuntimeError("A v2 component run could not be claimed atomically")
                claimed.append(run_id)
        active = store.save_document(
            REQUEST_KEY, {**request, "status": "running", "started_at": now()},
            request["revision"],
        )
        heartbeat(store, "running", detail={"training_id": request["id"]})
        result = train_candidate(store, active, siglip)
        completed = {
            **active, "status": "completed", "completed_at": now(),
            "release_id": result["release_id"],
            "production_ready": result["production_ready"],
        }
        store.save_document(REQUEST_KEY, completed, active["revision"])
        heartbeat(store, "ready", detail={"latest_release_id": result["release_id"]})
        return True
    except Exception as exc:
        for run_id in claimed:
            try:
                _fail_run(store, run_id, type(exc).__name__)
            except Exception:
                pass
        current = store.document(REQUEST_KEY) or request
        if current.get("id") == request.get("id") and current.get("status") in {"queued", "running"}:
            try:
                store.save_document(
                    REQUEST_KEY,
                    {**{k: v for k, v in current.items() if k != "revision"},
                     "status": "failed", "failed_at": now(),
                     "error_code": type(exc).__name__, "diagnostic": safe_error(exc),
                     "error": "ActVision v2 training failed; inspect saved stage diagnostics."},
                    current.get("revision", 0),
                )
            except Exception:
                pass
        heartbeat(store, "failed", detail={"error_code": type(exc).__name__})
        raise

"""Hash-bound ActVision v2 artifact loading and shared multimodal prediction."""
from __future__ import annotations

from importlib.metadata import version as package_version

from actvision_contract import unknown_result, validate_contract
from property_models import PhysicalModelAdapter, predict_components_v2
from release_bundle import FEATURE_SCHEMAS
from structured_model import completeness

_CACHE = {}


def release_manifest(store, release_id, *, allowed_statuses=("candidate", "shadow", "production")):
    with store.database.connect() as db:
        row = db.execute(
            "SELECT id,status,bundle_manifest,bundle_sha256,evaluation_summary "
            "FROM acq_training.model_releases WHERE workspace_id=%s AND id=%s",
            (store.workspace, release_id),
        ).fetchone()
    if not row or row["status"] not in allowed_statuses:
        raise ValueError("ActVision release is not available for this runtime")
    manifest = validate_contract(row["bundle_manifest"], "release")
    if manifest["release_id"] != str(row["id"]) or manifest["status"] != row["status"]:
        # Candidate manifests are immutable. Promotion creates an approved manifest
        # update in the same release row and must keep DB/manifest state aligned.
        raise ValueError("ActVision release registry and manifest disagree")
    return manifest


def load_models(store, release_id, *, allowed_statuses=("candidate", "shadow", "production")):
    manifest = release_manifest(store, release_id, allowed_statuses=allowed_statuses)
    identity = (store.workspace, release_id, tuple(
        (name, artifact["sha256"] if artifact else None)
        for name, artifact in sorted(manifest["components"].items())
    ))
    if identity in _CACHE:
        return manifest, _CACHE[identity]

    import joblib
    models = {}
    for name, artifact in manifest["components"].items():
        if artifact is None:
            continue
        if artifact["framework"] != "scikit-learn" or artifact["framework_version"] != package_version("scikit-learn"):
            raise ValueError(f"Unsupported {name} framework/version")
        if artifact["feature_schema_version"] not in FEATURE_SCHEMAS[name]:
            raise ValueError(f"Incompatible {name} feature schema")
        uri = artifact["uri"]
        if not uri.startswith("models/actvision-v2/") or ".." in uri.split("/") or "\\" in uri:
            raise ValueError("Model artifact path is outside the private v2 namespace")
        path = store.storage.get("acq-training-private", uri, artifact["sha256"])
        model = joblib.load(path)
        if getattr(model, "feature_schema_version", None) != artifact["feature_schema_version"]:
            raise ValueError(f"{name} artifact feature schema mismatch")
        if getattr(getattr(model, "calibrator", None), "version", None) != artifact["calibration_version"]:
            raise ValueError(f"{name} artifact calibration mismatch")
        models[name] = model
    _CACHE[identity] = models
    return manifest, models


def predict_local(
    store,
    siglip,
    release_id,
    *,
    remarks,
    structured,
    photos,
    selected_photo_count=None,
    allowed_statuses=("candidate", "shadow", "production"),
):
    """Predict from already-verified local image bytes.

    photos: [{"path": Path, "photo_id": str, "sha256": str, "room": str}]
    """
    manifest, models = load_models(store, release_id, allowed_statuses=allowed_statuses)
    adapters = {}
    for name, model in models.items():
        artifact = manifest["components"][name]
        adapters[name] = PhysicalModelAdapter(
            name, model, artifact["component_version"], artifact["calibration_version"]
        )

    bags = []
    if photos and "vision" in adapters:
        vectors = siglip.embed([photo["path"] for photo in photos])
        bags = [
            {"vector": vector, "room": photo.get("room") or "other"}
            for photo, vector in zip(photos, vectors)
        ]
    coverage = {
        "selected_photo_count": int(selected_photo_count if selected_photo_count is not None else len(photos)),
        "usable_photo_count": len(bags),
        "text_available": bool((remarks or "").strip()),
        "structured_completeness": completeness(structured or {}),
    }
    components = predict_components_v2(
        adapters, vectors=bags, remarks=remarks or "", structured=structured or {}, coverage=coverage
    )
    available_inputs = [
        name for name in ("vision", "text", "structured")
        if components[name]["status"] == "available"
    ]
    fusion = components["fusion"]
    if fusion["status"] == "available":
        result = fusion["result"]
        status = "complete" if len(available_inputs) == 3 else "partial"
        modalities = available_inputs
    elif len(available_inputs) == 1:
        result = components[available_inputs[0]]["result"]
        status = "partial"
        modalities = available_inputs
    else:
        result = unknown_result()
        status = "unavailable"
        modalities = []
    return {
        "manifest": manifest,
        "status": status,
        "components": components,
        "result": result,
        "modalities_used": modalities,
        "coverage": coverage,
    }

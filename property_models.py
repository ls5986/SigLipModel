"""Shared property-level features, modality routing, and prediction output."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, TYPE_CHECKING


if TYPE_CHECKING:
    import numpy as np

PHOTO_CONTEXTS = {
    "subject", "subject_interior", "subject_exterior", "shared_amenity",
    "floor_plan", "unrelated", "unknown",
}
EXCLUDED_CONTEXTS = {"shared_amenity", "floor_plan", "unrelated"}
PREDICTION_MODES = {"automatic", "images_only", "metadata_only", "images_and_metadata"}
V2_MODALITIES = ("vision", "text", "structured", "fusion")


@dataclass
class VisionEvidenceModel:
    """Physical heads over frozen SigLIP2 bags; legacy target heads stay separate."""
    heads: Any
    feature_schema_version: str = "actvision-siglip2-heads-v2"
    backbone: str = "google/siglip2-base-patch16-224"

    @classmethod
    def fit(cls, bags, labels):
        import numpy as np
        from structured_model import EvidenceHeads
        if len(bags) != len(labels):
            raise ValueError("Vision bags and labels must align")
        vectors = [vision_vector(bag) for bag in bags]
        present = [i for i, value in enumerate(vectors) if value is not None]
        if not present:
            raise ValueError("No usable subject image bags")
        return cls(EvidenceHeads.fit(np.stack([vectors[i] for i in present]), [labels[i] for i in present]))

    def predict(self, bags):
        from actvision_contract import unknown_result
        results = []
        for bag in bags:
            vector = vision_vector(bag)
            results.append(self.heads.predict(vector.reshape(1, -1))[0] if vector is not None else unknown_result())
        return results


@dataclass
class PhysicalModelAdapter:
    modality: str
    model: Any
    version: str
    calibration_version: str

    def __post_init__(self):
        from release_bundle import FEATURE_SCHEMAS
        if self.modality not in V2_MODALITIES or getattr(self.model, "feature_schema_version", None) not in FEATURE_SCHEMAS[self.modality]:
            raise ValueError("Only compatible v2 physical models can enter this adapter; legacy targets cannot")
        if not self.version or not self.calibration_version:
            raise ValueError("Adapters require model and calibration versions")

    def predict(self, evidence, coverage=None):
        if self.modality == "fusion":
            return self.model.predict([evidence], [coverage])[0]
        return self.model.predict([evidence])[0]


def predict_components_v2(adapters, *, vectors, remarks, structured, coverage):
    """Use only explicitly supplied physical-evidence adapters, never legacy target heads."""
    from actvision_contract import unavailable_component, _validate_result
    from structured_model import completeness

    inputs = {"vision": vectors, "text": remarks, "structured": structured}
    present = {"vision": bool(len(vectors)), "text": bool(remarks.strip()),
               "structured": completeness(structured) > 0}
    results = {}
    for name in V2_MODALITIES:
        if name != "fusion" and not present[name]:
            results[name] = unavailable_component("missing", f"No {name} evidence")
        elif name not in adapters:
            results[name] = unavailable_component("unavailable", f"No trained v2 {name} artifact")
        elif name == "fusion" and not any(c["status"] == "available" for c in results.values()):
            results[name] = unavailable_component("missing", "No available components for fusion")
        else:
            adapter = adapters[name]
            result = adapter.predict(results, coverage) if name == "fusion" else adapter.predict(inputs[name])
            _validate_result(result)
            results[name] = {
                "status": "available", "version": adapter.version,
                "calibration_version": adapter.calibration_version,
                "result": result, "reason": None,
            }
    return results

METADATA_FIELDS = {
    "year_built": (("YearBuilt", "year_built"), "number"),
    "bedrooms": (("BedroomsTotal", "bedrooms", "beds"), "number"),
    "bathrooms": (("BathroomsTotalInteger", "BathroomsTotalDecimal", "bathrooms", "baths"), "number"),
    "living_area": (("LivingArea", "living_area", "sqft"), "number"),
    "lot_size": (("LotSizeArea", "LotSizeSquareFeet", "lot_size"), "number"),
    "property_type": (("PropertySubType", "PropertyType", "property_type"), "category"),
    "city": (("City", "city"), "category"),
    "state": (("StateOrProvince", "state"), "category"),
    "postal_code": (("PostalCode", "postal_code"), "postal"),
}
ACCEPTED_METADATA_KEYS = frozenset(
    alias for aliases, _ in METADATA_FIELDS.values() for alias in aliases
)


def normalize_context(value: Any) -> str:
    value = str(value or "unknown")
    return value if value in PHOTO_CONTEXTS else "unknown"


def usable_context(value: Any) -> bool:
    return normalize_context(value) not in EXCLUDED_CONTEXTS


def _first(metadata: dict, aliases: tuple[str, ...]):
    for key in aliases:
        value = metadata.get(key)
        if value is not None and value != "":
            return value
    return None


def metadata_features(metadata: dict | None) -> dict[str, float | str]:
    metadata = metadata if isinstance(metadata, dict) else {}
    result: dict[str, float | str] = {}
    for name, (aliases, kind) in METADATA_FIELDS.items():
        value = _first(metadata, aliases)
        result[name + "_missing"] = 1.0
        if value is None:
            continue
        if kind == "number":
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if math.isfinite(number):
                result[name] = number
                result[name + "_missing"] = 0.0
        else:
            text = str(value).strip().casefold()
            if text:
                result[name] = text[:5] if kind == "postal" else text[:100]
                result[name + "_missing"] = 0.0
    return result


def metadata_completeness(metadata: dict | None) -> float:
    features = metadata_features(metadata)
    present = sum(not bool(features.get(name + "_missing", 1.0)) for name in METADATA_FIELDS)
    return present / len(METADATA_FIELDS)


def target_class(review: dict | None) -> int | None:
    if not isinstance(review, dict) or review.get("status") != "approved":
        return None
    score = review.get("target_score")
    if type(score) is int:
        if score >= 4:
            return 1
        if score <= 2:
            return 0
        return None
    return {"target": 1, "not_target": 0}.get(review.get("target_fit"))


def vision_vector(vectors: list[np.ndarray] | np.ndarray) -> np.ndarray | None:
    import numpy as np
    if isinstance(vectors, np.ndarray):
        values = vectors
    elif vectors:
        values = np.stack(vectors)
    else:
        return None
    if values.ndim != 2 or not len(values):
        return None
    return np.concatenate([
        values.mean(axis=0),
        values.max(axis=0),
        np.asarray([math.log1p(len(values))], dtype=values.dtype),
    ])


def positive_probability(model, values) -> float:
    classes = list(model.classes_)
    if 1 not in classes:
        raise ValueError("Property model has no positive class")
    return float(model.predict_proba(values)[0, classes.index(1)])


def _component_scores(bundle: dict, vectors: list[np.ndarray], metadata: dict) -> tuple[dict, list[str]]:
    import numpy as np
    models = bundle.get("property_models", {})
    scores: dict[str, float | None] = {"images": None, "metadata": None, "combined": None}
    warnings: list[str] = []
    pooled = vision_vector(vectors)
    if pooled is not None and models.get("vision") is not None:
        scores["images"] = positive_probability(models["vision"], pooled.reshape(1, -1))
    elif pooled is None:
        warnings.append("No usable subject photos")
    else:
        warnings.append("This candidate has no trained property vision component")

    fields = metadata_features(metadata)
    has_metadata = any(not key.endswith("_missing") for key in fields)
    if has_metadata and models.get("metadata") is not None:
        scores["metadata"] = positive_probability(models["metadata"], [fields])
    elif not has_metadata:
        warnings.append("No supported metadata fields")
    else:
        warnings.append("This candidate has no trained metadata component")

    if scores["images"] is not None and scores["metadata"] is not None:
        fusion = models.get("fusion")
        if fusion is not None:
            row = np.asarray([[
                scores["images"], scores["metadata"], math.log1p(len(vectors)),
                metadata_completeness(metadata),
            ]])
            scores["combined"] = positive_probability(fusion, row)
        else:
            warnings.append("This candidate has no trained fusion component")
    return scores, warnings


def predict_property(bundle: dict, vectors: list[np.ndarray], metadata: dict | None,
                     requested_mode: str = "automatic") -> dict:
    if requested_mode not in PREDICTION_MODES:
        raise ValueError("Unsupported prediction mode")
    metadata = metadata if isinstance(metadata, dict) else {}
    if bundle.get("target_similarity"):
        from target_similarity import predict_similarity
        return predict_similarity(bundle["target_similarity"], vectors, metadata, requested_mode)
    scores, warnings = _component_scores(bundle, vectors, metadata)
    available = {
        "images_only": scores["images"],
        "metadata_only": scores["metadata"],
        "images_and_metadata": scores["combined"],
    }
    if requested_mode == "automatic":
        mode = next((name for name in ("images_and_metadata", "images_only", "metadata_only")
                     if available[name] is not None), "insufficient_evidence")
    else:
        mode = requested_mode if available[requested_mode] is not None else "insufficient_evidence"
        if mode == "insufficient_evidence":
            warnings.append(f"Requested mode {requested_mode} is unavailable for this evidence and model version")
    score = available.get(mode)
    confidence = "low"
    if mode == "images_and_metadata" and len(vectors) >= 3 and metadata_completeness(metadata) >= .5:
        confidence = "high"
    elif mode != "insufficient_evidence" and (len(vectors) >= 2 or metadata_completeness(metadata) >= .4):
        confidence = "medium"
    return {
        "requested_mode": requested_mode,
        "mode_used": mode,
        "score": score,
        "component_scores": scores,
        "evidence_confidence": confidence,
        "usable_photos": len(vectors),
        "metadata_completeness": metadata_completeness(metadata),
        "warnings": list(dict.fromkeys(warnings)),
        "decision": "NEEDS_REVIEW",
    }


def evaluation_slices(properties: list[dict]) -> dict[str, list[dict]]:
    """Human condition and fit are separate evaluation axes, never input features."""
    decisive = [p for p in properties if p.get("split") == "test"
                and target_class(p.get("review")) is not None]
    def condition_group(review):
        if "physical_condition" in review or review.get("label_schema_version") == "actvision-labels-v2":
            physical = review.get("physical_condition", "UNKNOWN")
            if physical in {"C1_NEW", "C2_LIKE_NEW", "C3_WELL_MAINTAINED"}:
                return "maintained"
            if physical in {"C5_REHAB_NEEDED", "C6_SEVERE_DISTRESS"}:
                return "rough"
            return None
        if review.get("condition_label") in {"updated", "slightly_dated", "maintained_original"}:
            return "maintained"
        if review.get("condition_label") in {"rough", "major"}:
            return "rough"
        return None

    return {
        "maintained_targets": [p for p in decisive if target_class(p["review"]) == 1
                               and condition_group(p["review"]) == "maintained"],
        "rough_targets": [p for p in decisive if target_class(p["review"]) == 1
                          and condition_group(p["review"]) == "rough"],
        "rough_non_targets": [p for p in decisive if target_class(p["review"]) == 0
                              and condition_group(p["review"]) == "rough"],
        "maintained_non_targets": [p for p in decisive if target_class(p["review"]) == 0
                                   and condition_group(p["review"]) == "maintained"],
        "pricing_or_characteristics": [p for p in decisive
                                       if p["review"].get("fit_basis") in {"pricing", "layout_location"}],
    }

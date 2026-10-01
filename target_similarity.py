"""Versioned, positive-only reference index. Scores are similarity, not probability."""
import math
import numpy as np

from property_models import METADATA_FIELDS, metadata_features, metadata_completeness

OBJECTIVE = "known-target-similarity-v1"
NUMERIC_FLOORS = {"year_built": 10, "bedrooms": 1, "bathrooms": 1,
                  "living_area": 200, "lot_size": 500}


def image_signature(vectors):
    if not len(vectors):
        return None
    values = np.asarray(vectors, dtype=float)
    if values.ndim != 2 or not np.isfinite(values).all():
        raise ValueError("Invalid reference embeddings")
    norms = np.linalg.norm(values, axis=1, keepdims=True)
    values = values / np.maximum(norms, 1e-12)
    pooled = values.mean(axis=0)
    norm = np.linalg.norm(pooled)
    return pooled / norm if norm > 1e-12 else None


def fit_reference_index(properties, vectors_by_property):
    references = []
    for prop in properties:
        if not (prop.get("known_target") and prop.get("training_allowed")
                and prop.get("split") != "test" and not prop.get("label_exclusion")):
            continue
        signature = None if prop.get("photo_coverage")=="no_interior" else image_signature(vectors_by_property.get(prop["id"], []))
        fields = metadata_features(prop.get("metadata"))
        if signature is None and not any(not k.endswith("_missing") for k in fields):
            continue
        references.append({"property_id": prop["id"], "group_id": prop["group_id"],
                           "source_rows": prop.get("source_rows", []),
                           "images": signature, "metadata": fields})
    if len({r["group_id"] for r in references}) < 5:
        raise ValueError("Need at least five independent verified target groups")
    scales = {}
    for field, floor in NUMERIC_FLOORS.items():
        values = [r["metadata"][field] for r in references if field in r["metadata"]]
        scales[field] = max(floor, float(np.subtract(*np.percentile(values, [75, 25]))) if values else floor)
    return {"objective": OBJECTIVE, "references": references, "numeric_scales": scales,
            "combined_weights": {"images": .5, "metadata": .5}}


def metadata_similarity(query, reference, scales):
    shared = [name for name in METADATA_FIELDS if name in query and name in reference]
    # One matching categorical field must not produce a misleading perfect score.
    if len(shared) < 3:
        return None, shared
    scores = [math.exp(-abs(query[name] - reference[name]) / scales[name])
              if name in scales else float(query[name] == reference[name]) for name in shared]
    return float(np.mean(scores)), shared


def predict_similarity(index, vectors, metadata, requested_mode="automatic", exclude_group=None):
    signature = image_signature(vectors)
    fields = metadata_features(metadata)
    neighbors = []
    for ref in index["references"]:
        if ref["group_id"] == exclude_group:
            continue
        image_score = None
        if signature is not None and ref["images"] is not None:
            image_score = float(np.clip(np.dot(signature, ref["images"]), 0, 1))
        meta_score, shared = metadata_similarity(fields, ref["metadata"], index["numeric_scales"])
        combined = (image_score + meta_score) / 2 if image_score is not None and meta_score is not None else None
        neighbors.append({"property_id": ref["property_id"], "group_id": ref["group_id"],
                          "source_rows": ref["source_rows"], "shared_metadata_fields": shared,
                          "component_scores": {"images": image_score, "metadata": meta_score, "combined": combined}})
    key_for_mode = {"images_only": "images", "metadata_only": "metadata", "images_and_metadata": "combined"}
    mode = requested_mode
    if mode == "automatic":
        mode = next((name for name in ("images_and_metadata", "images_only", "metadata_only")
                     if any(n["component_scores"][key_for_mode[name]] is not None for n in neighbors)), "insufficient_evidence")
    key = key_for_mode.get(mode)
    ordered = sorted([n for n in neighbors if key and n["component_scores"][key] is not None],
                     key=lambda n: (-n["component_scores"][key], n["property_id"]))
    # Do not let repeated sales/listings of one physical property occupy every slot.
    unique, seen = [], set()
    for neighbor in ordered:
        if neighbor["group_id"] not in seen:
            seen.add(neighbor["group_id"])
            unique.append(neighbor)
    if not unique:
        mode = "insufficient_evidence"
    best = unique[0] if unique else None
    scores = best["component_scores"] if best else {"images": None, "metadata": None, "combined": None}
    return {"requested_mode": requested_mode, "mode_used": mode,
            "score": scores.get(key) if best else None, "component_scores": scores,
            "score_kind": "known_target_similarity", "nearest_examples": unique[:5],
            "reference_properties": len(index["references"]),
            "evidence_confidence": "low", "usable_photos": len(vectors),
            "metadata_completeness": metadata_completeness(metadata),
            "warnings": ["Similarity to verified known targets; not target probability or profitability.",
                         "Metadata similarity requires at least three shared descriptive fields."]
                        + (["Requested evidence mode is unavailable"] if not best else []),
            "decision": "NEEDS_REVIEW"}


def evaluate_reference_index(index, properties, vectors_by_property):
    rows = []
    for prop in properties:
        if prop.get("split") != "test" or not prop.get("known_target") or prop.get("label_exclusion"):
            continue
        result = predict_similarity(index, [] if prop.get("photo_coverage")=="no_interior" else vectors_by_property.get(prop["id"], []),
                                    prop.get("metadata"), exclude_group=prop["group_id"])
        rows.append({"property_id": prop["id"], "group_id": prop["group_id"],
                     "mode_used": result["mode_used"], "score": result["score"],
                     "component_scores": result["component_scores"],
                     "nearest_examples": result["nearest_examples"]})
    summary = {}
    for component in ("images", "metadata", "combined"):
        values = [r["component_scores"][component] for r in rows if r["component_scores"][component] is not None]
        summary[component] = {"n": len(values), "median_similarity": float(np.median(values)) if values else None}
    return {"heldout_properties": len(rows), "heldout_groups": len({r["group_id"] for r in rows}),
            "components": summary, "properties": rows,
            "limitation": "Positive-only heldout coverage and resemblance. No classifier accuracy, AUC or false-positive claim."}

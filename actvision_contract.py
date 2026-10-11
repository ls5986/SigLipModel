"""Canonical v2 wire validation; UNKNOWN is never a negative training example."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = "actvision-v2"
MODALITIES = ("vision", "text", "structured", "fusion")
SCHEMA_PATH = Path(__file__).parent / "contracts" / "actvision-v2.schema.json"
SCHEMA = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def canonical_json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def remarks_digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def unknown_result():
    return {
        "physical_condition": "UNKNOWN", "modernization": "UNKNOWN", "acquisition_fit": "UNKNOWN",
        "condition_probabilities": {}, "modernization_probabilities": {}, "acquisition_fit_probabilities": {},
        "value_add_score": None, "confidence": None, "text_signals": [],
    }


def unavailable_component(status, reason):
    if status not in {"missing", "unavailable", "failed"} or not reason:
        raise ValueError("An unavailable component needs an explicit status and reason")
    return {"status": status, "version": None, "calibration_version": None,
            "result": unknown_result(), "reason": reason}


def _text_evidence(item, remarks=None):
    start, end, snippet = item["start"], item["end"], item["snippet"]
    if item["state"] == "UNKNOWN" and item["probability"] is not None:
        raise ValueError("UNKNOWN text signal cannot have a probability")
    if snippet is None:
        if start is not None or end is not None:
            raise ValueError("Text spans require a snippet")
    elif start is None or end is None or end <= start or len(snippet) != end - start:
        raise ValueError("Text span must match the snippet (Unicode code-point offsets)")
    elif remarks is not None and remarks[start:end] != snippet:
        raise ValueError("Text snippet does not match source remarks")


def validate_contract(payload, kind=None):
    if not isinstance(payload, dict):
        raise ValueError("Expected an ActVision object")
    canonical_json(payload)
    kind = kind or payload.get("kind")
    if kind not in {"inference_request", "prediction", "feedback", "release"} or payload.get("kind") != kind:
        raise ValueError("Unsupported ActVision payload kind")
    validator = Draft202012Validator(
        {"$schema": SCHEMA["$schema"], "$defs": SCHEMA["$defs"], "$ref": f"#/$defs/{kind}"},
        format_checker=FormatChecker(),
    )
    errors = sorted(validator.iter_errors(payload), key=lambda e: str(list(e.path)))
    if errors:
        raise ValueError(f"Invalid {kind} at {'.'.join(map(str, errors[0].path))}: {errors[0].message}")
    if "evidence" in payload:
        evidence = payload["evidence"]
        if digest(evidence) != payload["evidence_id"]:
            raise ValueError("Evidence identity hash mismatch")
        if "release_id" in payload and payload["release_id"] != evidence["release_id"]:
            raise ValueError("Release identity mismatch")
        ids = [p["photo_id"] for p in evidence["selected_photos"]]
        if len(ids) != len(set(ids)):
            raise ValueError("Duplicate selected photo IDs")
    if kind == "inference_request":
        if remarks_digest(payload["public_remarks"]) != evidence["remarks_sha256"]:
            raise ValueError("Remarks hash mismatch")
        if digest(payload["structured"]) != evidence["structured_sha256"]:
            raise ValueError("Structured feature hash mismatch")
        if [p["photo_id"] for p in payload["photo_inputs"]] != ids:
            raise ValueError("Photo inputs must match selected photos in order")
        for photo, identity in zip(payload["photo_inputs"], evidence["selected_photos"]):
            if remarks_digest(photo["uri"]) != identity["uri_sha256"]:
                raise ValueError("Photo URI hash mismatch")
    if kind == "prediction":
        for name, component in payload["components"].items():
            _validate_result(component["result"])
            if component["status"] != "available":
                if component["result"] != unknown_result() or not component["reason"]:
                    raise ValueError("Missing/unavailable/failed components must remain UNKNOWN")
            elif not component["version"] or not component["calibration_version"]:
                raise ValueError("Available components must pin model and calibration versions")
        _validate_result(payload["result"])
        if any(payload["components"][m]["status"] != "available" for m in payload["modalities_used"]):
            raise ValueError("Used modalities must be available")
        if payload["status"] in {"unavailable", "failed"} and (
            payload["result"] != unknown_result() or payload["modalities_used"]
        ):
            raise ValueError("Unavailable predictions must remain UNKNOWN")
        available = [name for name in ("vision", "text", "structured") if payload["components"][name]["status"] == "available"]
        if payload["status"] in {"complete", "partial"} and not available:
            raise ValueError("Successful predictions need an available input component")
        if payload["status"] == "complete" and (len(available) != 3 or payload["components"]["fusion"]["status"] != "available"):
            raise ValueError("Complete predictions require all four components")
        fusion = payload["components"]["fusion"]
        if fusion["status"] == "available" and payload["result"] != fusion["result"]:
            raise ValueError("Top-level result must match the available fusion result")
        if fusion["status"] != "available" and payload["result"] != unknown_result():
            if len(payload["modalities_used"]) != 1 or payload["result"] != payload["components"][payload["modalities_used"][0]]["result"]:
                raise ValueError("An unfused result must explicitly identify its single fallback modality")
        if payload["coverage"]["selected_photo_count"] != len(ids) or payload["coverage"]["usable_photo_count"] > len(ids):
            raise ValueError("Photo coverage does not match evidence")
        observed = payload["observed_photo_hashes"]
        if len({p["photo_id"] for p in observed}) != len(observed) or any(p["photo_id"] not in ids for p in observed):
            raise ValueError("Observed photos must be a unique subset of selected evidence")
        supplied_hashes = {p["photo_id"]: p["sha256"] for p in evidence["selected_photos"]}
        if any(supplied_hashes[p["photo_id"]] not in {None, p["sha256"]} for p in observed):
            raise ValueError("Observed image content hash differs from requested evidence")
        if payload["coverage"]["usable_photo_count"] > len(observed):
            raise ValueError("Used photos require observed byte hashes")
        if payload["components"]["vision"]["status"] == "available" and not payload["coverage"]["usable_photo_count"]:
            raise ValueError("Available vision needs usable photo evidence")
    if kind == "feedback":
        for correction in payload["corrections"]:
            if correction["type"] == "text_signal":
                _text_evidence(correction["value"])
            if correction["type"] == "photo":
                if correction["photo_id"] not in ids:
                    raise ValueError("Photo correction is not part of prediction evidence")
                if correction["usable"] == (correction["exclusion_reason"] is not None):
                    raise ValueError("Excluded photos require a reason; usable photos cannot have one")
                if correction["usable"] and correction["context"] in {"shared_amenity", "floor_plan", "unrelated"}:
                    raise ValueError("Non-subject photos cannot be usable condition evidence")
    if kind == "release" and payload["status"] in {"shadow", "production"}:
        if not payload["approved_by"] or not payload["approved_at"] or not payload["evaluation"]["protected_slices_passed"]:
            raise ValueError("Approved releases require reviewer, timestamp and protected-slice evaluation")
        if not any(payload["components"].values()):
            raise ValueError("Approved releases require trained artifacts")
    return payload


def _validate_result(result):
    validator = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/physical_result"})
    error = next(validator.iter_errors(result), None)
    if error:
        raise ValueError("Invalid physical result: " + error.message)
    for key in ("condition_probabilities", "modernization_probabilities", "acquisition_fit_probabilities"):
        values = result[key]
        if values and abs(sum(values.values()) - 1.0) > 1e-6:
            raise ValueError("Class probabilities must sum to one")
    for item in result["text_signals"]:
        _text_evidence(item)
    signals = [item["signal"] for item in result["text_signals"]]
    if len(signals) != len(set(signals)):
        raise ValueError("Duplicate text signals")

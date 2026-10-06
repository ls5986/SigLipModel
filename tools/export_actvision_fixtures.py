"""Generate deterministic synthetic cross-repository conformance fixtures."""
import json
import sys
from copy import deepcopy
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from actvision_contract import digest, remarks_digest, unavailable_component, unknown_result, validate_contract


def fixtures():
    evidence = {
        "workspace_id": "fixture-workspace", "property_id": "fixture-property",
        "listing_id": "fixture-listing", "snapshot_at": "2026-10-02T00:00:00Z",
        "visual_generation": 0, "photos_change_timestamp": None,
        "selected_photos": [], "remarks_sha256": remarks_digest(""),
        "structured_sha256": digest({}), "release_id": "fixture-candidate-v2",
    }
    request = {
        "kind": "inference_request", "schema_version": "actvision-v2",
        "request_id": "fixture-request", "evidence_id": digest(evidence), "evidence": evidence,
        "public_remarks": "", "structured": {}, "photo_inputs": [],
    }
    prediction = {
        "kind": "prediction", "schema_version": "actvision-v2", "label_schema_version": "actvision-labels-v2",
        "prediction_id": "fixture-prediction", "request_id": request["request_id"],
        "evidence_id": request["evidence_id"], "evidence": evidence, "release_id": evidence["release_id"],
        "status": "unavailable", "components": {
            name: unavailable_component("missing" if name != "fusion" else "unavailable",
                                        "No input evidence" if name != "fusion" else "No trained fusion artifact")
            for name in ("vision", "text", "structured", "fusion")
        }, "result": unknown_result(), "modalities_used": [],
        "coverage": {"selected_photo_count": 0, "usable_photo_count": 0, "text_available": False,
                     "structured_completeness": 0},
        "observed_photo_hashes": [], "created_at": "2026-10-02T00:00:01Z", "latency_ms": 0,
        "warnings": ["Synthetic conformance fixture; not a trained prediction"],
    }
    feedback = {
        "kind": "feedback", "schema_version": "actvision-v2", "label_schema_version": "actvision-labels-v2",
        "event_id": "fixture-feedback", "source": "mls_sourcing",
        "source_prediction_id": prediction["prediction_id"],
        "evidence_id": request["evidence_id"], "evidence": evidence, "release_id": evidence["release_id"],
        "reviewer": {"id": "fixture-reviewer", "role": "reviewer"}, "reviewed_at": "2026-10-02T00:01:00Z",
        "corrections": [{"type": "physical_condition", "value": "UNKNOWN"},
                        {"type": "acquisition_fit", "value": "UNKNOWN", "reason_tags": ["insufficient_evidence"]}],
        "note": "Synthetic feedback only", "supersedes_event_id": None,
    }
    release = {
        "kind": "release", "schema_version": "actvision-v2", "label_schema_version": "actvision-labels-v2",
        "release_id": evidence["release_id"], "status": "candidate",
        "components": dict.fromkeys(("vision", "text", "structured", "fusion")),
        "dataset_sha256": digest({"fixture": "dataset"}),
        "evaluation": {"report_sha256": digest({"fixture": "evaluation"}), "protected_slices_passed": False},
        "approved_by": None, "approved_at": None,
    }
    full_request = deepcopy(request)
    full_request["request_id"] = "fixture-multimodal-request"
    full_request["public_remarks"] = "Bring your vision."
    full_request["structured"] = {"YearBuilt": 1963, "PropertyType": "Residential"}
    uri = "https://fixture.invalid/synthetic-photo"
    full_request["photo_inputs"] = [{"photo_id": "fixture-photo", "uri": uri}]
    full_request["evidence"].update(
        selected_photos=[{"photo_id": "fixture-photo", "sha256": None, "uri_sha256": remarks_digest(uri)}],
        remarks_sha256=remarks_digest(full_request["public_remarks"]),
        structured_sha256=digest(full_request["structured"]),
        release_id="fixture-shadow-v2",
    )
    full_request["evidence_id"] = digest(full_request["evidence"])
    result = {
        **unknown_result(), "physical_condition": "C3_WELL_MAINTAINED", "modernization": "ORIGINAL",
        "acquisition_fit": "TARGET",
        "condition_probabilities": {"C3_WELL_MAINTAINED": .75, "C4_AVERAGE_FUNCTIONAL": .25},
        "modernization_probabilities": {"ORIGINAL": .8, "PARTIALLY_UPDATED": .2},
        "acquisition_fit_probabilities": {"TARGET": .6, "NOT_TARGET": .4},
        "value_add_score": .6, "confidence": .7,
    }
    complete = deepcopy(prediction)
    complete.update(
        prediction_id="fixture-complete-prediction", request_id=full_request["request_id"],
        evidence=full_request["evidence"], evidence_id=full_request["evidence_id"], release_id="fixture-shadow-v2",
        status="complete", result=deepcopy(result), modalities_used=["vision", "text", "structured"],
        coverage={"selected_photo_count": 1, "usable_photo_count": 1, "text_available": True, "structured_completeness": 2 / 15},
        observed_photo_hashes=[{"photo_id": "fixture-photo", "sha256": remarks_digest("synthetic image bytes")}],
    )
    for name in complete["components"]:
        complete["components"][name] = {
            "status": "available", "version": f"fixture-{name}-1",
            "calibration_version": "fixture-calibration-1", "result": deepcopy(result), "reason": None,
        }
    complete["components"]["text"]["result"]["text_signals"] = [{
        "signal": "clear_slate_or_blank_canvas", "state": "PRESENT", "probability": .8,
        "snippet": "Bring your vision", "start": 0, "end": 17,
    }]
    shadow = deepcopy(release)
    shadow.update(release_id="fixture-shadow-v2", status="shadow", approved_by="fixture-operator", approved_at="2026-10-02T00:00:00Z")
    shadow["evaluation"]["protected_slices_passed"] = True
    for name in shadow["components"]:
        shadow["components"][name] = {
            "uri": f"{name}.bin", "sha256": remarks_digest(f"synthetic {name} artifact"),
            "framework": "synthetic-fixture", "framework_version": "1",
            "component_version": f"fixture-{name}-1",
            "feature_schema_version": {
                "vision": "actvision-siglip2-heads-v2", "text": "actvision-text-tfidf-v2",
                "structured": "actvision-structured-v2", "fusion": "actvision-fusion-v2",
            }[name],
            "label_schema_version": "actvision-labels-v2", "calibration_version": "fixture-calibration-1",
        }
    return {
        "inference-request": request, "prediction-unavailable": prediction, "feedback": feedback,
        "release-candidate": release, "inference-request-multimodal": full_request,
        "prediction-complete": complete, "release-shadow": shadow,
    }


if __name__ == "__main__":
    target = Path(__file__).resolve().parents[1] / "contracts" / "fixtures"
    target.mkdir(exist_ok=True)
    for name, value in fixtures().items():
        validate_contract(value)
        text = json.dumps(value, indent=2, ensure_ascii=True) + "\n"
        path = target / f"{name}.json"
        if "--check" in sys.argv:
            if path.read_text(encoding="utf-8") != text:
                raise SystemExit(f"Fixture drift: {path}")
        else:
            path.write_text(text, encoding="utf-8")
    print(f"Validated {len(fixtures())} ActVision v2 fixtures")

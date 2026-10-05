"""Task-oriented Studio API layered on the existing evidence and training machinery."""
import os
from urllib.parse import parse_qs, quote, urlparse

from actvision_contract import digest, remarks_digest, _text_evidence
from condition_schema import LABEL_SCHEMA_V2, PHYSICAL_CONDITIONS, MODERNIZATION_STATES, TARGET_LABELS, TEXT_SIGNALS

TRAINING_ACTIONS = {
    "/api/studio/workbench/dataset/freeze", "/api/studio/workbench/train",
    "/api/studio/train", "/api/studio/v2/dataset/freeze", "/api/studio/v2/train",
}


def identity():
    role = os.environ.get("STUDIO_ROLE", "reviewer")
    if role not in {"reviewer", "operator", "admin"}:
        raise ValueError("STUDIO_ROLE must be reviewer, operator or admin")
    return {"id": os.environ.get("STUDIO_LOGIN_USERNAME") or os.environ.get("STUDIO_REVIEWER_ID") or "local-reviewer",
            "role": role}


def require_operator():
    if identity()["role"] not in {"operator", "admin"}:
        raise PermissionError("Only an operator or administrator may freeze datasets or train candidates")


def label_evidence(property_id, remarks, photos, metadata=None):
    return digest({"property_id": property_id, "remarks_sha256": remarks_digest(remarks),
                   "metadata": metadata or {},
                   "photos": sorted([{
                       "id": photo["id"], "sha256": photo.get("sha256"),
                       "included": photo.get("selection", {}).get("included", True),
                       "context": photo.get("effective", {}).get("context", "unknown"),
                       "review_revision": (photo.get("review") or {}).get("revision", 0),
                   } for photo in photos], key=lambda p: p["id"])})


def validate_text_reviews(items, remarks):
    from actvision_contract import SCHEMA
    from jsonschema import Draft202012Validator
    if not isinstance(items, list) or len(items) > len(TEXT_SIGNALS):
        raise ValueError("Invalid text reviews")
    validator = Draft202012Validator({"$defs": SCHEMA["$defs"], "$ref": "#/$defs/text_evidence"})
    seen = set()
    for item in items:
        if not validator.is_valid(item):
            raise ValueError("Invalid typed text evidence")
        _text_evidence(item, remarks)
        if item["signal"] in seen:
            raise ValueError("Duplicate text review")
        seen.add(item["signal"])
    return items


def get(studio, raw_path):
    parsed = urlparse(raw_path)
    args = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    action = parsed.path.removeprefix("/api/studio/v2/")
    cloud = hasattr(studio.store, "database")
    if action == "capabilities":
        actor = identity()
        return {"token": studio.app.token, "actor": actor, "cloud": cloud,
                "training": cloud and actor["role"] in {"operator", "admin"},
                "taxonomy": {"physical_condition": PHYSICAL_CONDITIONS, "modernization": MODERNIZATION_STATES,
                             "acquisition_fit": TARGET_LABELS, "text_signals": TEXT_SIGNALS},
                "label_schema_version": LABEL_SCHEMA_V2,
                "blockers": [
                    "V2 physical/text/fusion training orchestration and calibrated release artifacts are not provisioned.",
                    "Legacy Train Candidate is a target classifier, not a v2 physical evidence release.",
                    "Production corrections need identity/era mapping before frozen training dataset inclusion.",
                    "Promotion/rollback remain administrative and disabled here; protected-slice acceptance thresholds are not configured.",
                ]}
    if action == "property":
        detail = studio.get("/api/studio/property?id=" + quote(args.get("id", ""), safe=""))
        prop = detail["property"]
        detail["label_evidence_id"] = label_evidence(prop["id"], prop.get("mls_remarks") or "", detail["images"], prop.get("metadata"))
        return detail
    if action == "label/proposal":
        from typed_label_assistant import result
        detail = get(studio, "/api/studio/v2/property?id=" + quote(args.get("id", ""), safe=""))
        return result(studio.store, detail)
    if action == "feedback":
        if not cloud:
            return {"items": [], "unavailable": "Production feedback requires the reviewed Supabase v2 migration"}
        from actvision_service import feedback_queue
        return feedback_queue(studio.store)
    if action == "releases":
        if not cloud:
            return {"items": [], "unavailable": "Release registry requires the cloud training backend"}
        with studio.store.database.connect() as db:
            rows = db.execute("""SELECT id,name,version,status,evaluation_summary,bundle_manifest,approved_by,approved_at
                FROM acq_training.model_releases WHERE workspace_id=%s ORDER BY created_at DESC LIMIT 50""",
                (studio.store.workspace,)).fetchall()
        return {"items": [{**dict(row), "id": str(row["id"]), "approved_at": str(row["approved_at"]) if row["approved_at"] else None} for row in rows],
                "promotion_available": False, "reason": "Operator-reviewed calibrated bundles and protected-slice policy required; no promotion endpoint enabled"}
    if action == "operations":
        if cloud:
            from model_workbench import summary
            return {**summary(studio.store), "inference_enabled": False,
                    "notice": "No training or inference starts when this page opens."}
        return {"inference_enabled": False, "notice": "Local research backend; cloud training status unavailable"}
    raise ValueError("Unknown Studio v2 endpoint")


def post(studio, path, payload):
    action = path.removeprefix("/api/studio/v2/")
    cloud = hasattr(studio.store, "database")
    if action == "label/propose":
        if not cloud:
            raise ValueError("Hosted draft requests require cloud storage")
        from typed_label_assistant import queue
        detail = get(studio, "/api/studio/v2/property?id=" + quote(str(payload.get("id", "")), safe=""))
        return queue(studio.store, detail, payload, identity()["id"])
    if action == "label":
        detail = get(studio, "/api/studio/v2/property?id=" + quote(str(payload.get("id", "")), safe=""))
        if payload.get("label_evidence_id") != detail["label_evidence_id"]:
            raise RuntimeError("Evidence changed; reload before saving labels")
        if (detail.get("historical_source") or {}).get("blocked"):
            raise ValueError("Acquisition evidence is quarantined; verify the source/era first")
        validate_text_reviews(payload.get("text_signals", []), detail["property"].get("mls_remarks") or "")
        assisted = None
        if payload.get("assistant_input_sha256"):
            from typed_label_assistant import result
            proposal = result(studio.store, detail)["proposal"]
            if not proposal or proposal.get("input_sha256") != payload["assistant_input_sha256"]:
                raise RuntimeError("Model draft changed; reload before approving")
            assisted = {k:proposal[k] for k in ("proposal_id", "input_sha256", "model", "policy", "label_evidence_id")}
        return studio.post("/api/studio/review", {
            **payload, "kind": "property", "label_schema_version": LABEL_SCHEMA_V2,
            "reviewer": identity()["id"], "assistant_proposal":assisted,
        })
    if action == "feedback/review":
        if not cloud:
            raise ValueError("Feedback review requires cloud storage")
        from actvision_service import decide_feedback
        return decide_feedback(studio.store, payload, identity()["id"])
    if action in {"dataset/preview", "dataset/freeze", "train"}:
        if not cloud:
            raise ValueError("Explicit frozen-dataset training requires the cloud worker")
        require_operator()
        if action == "dataset/preview":
            from typed_dataset import preview
            return preview(studio.store)
        raise ValueError("V2 freeze/training requires grouped orchestration and durable bundles; legacy training is not a v2 candidate")
    raise ValueError("Unknown Studio v2 action")

"""Read-only v2 coverage; cohort membership is never human ground truth."""
from collections import Counter, defaultdict

from actvision_contract import digest
from condition_schema import LABEL_SCHEMA_V2, PHYSICAL_CONDITIONS, MODERNIZATION_STATES, TEXT_SIGNALS


def preview(store, properties=None):
    if properties is None:
        from cloud_training import snapshot
        _, properties = snapshot(store)
    from studio_v2 import label_evidence, validate_text_reviews
    candidates, excluded, drafts = [], [], 0
    for prop in properties:
        review = prop.get("review") or {}
        if review.get("status") != "approved" or review.get("label_schema_version") != LABEL_SCHEMA_V2:
            drafts += bool(review)
            continue
        reason = prop.get("label_exclusion") or (None if prop.get("timing_verified") else "Acquisition era unverified")
        detail = store.property(prop["id"])
        current = detail["property"]
        evidence = label_evidence(prop["id"], current.get("mls_remarks") or "", detail["images"], current.get("metadata"))
        if evidence != review.get("label_evidence_id"):
            reason = "Evidence changed after approval"
        if (detail.get("historical_source") or {}).get("blocked"):
            reason = "Source/era quarantined"
        if not review.get("reviewer"):
            reason = "Human reviewer identity missing"
        try:
            validate_text_reviews(review.get("text_signals", []), current.get("mls_remarks") or "")
        except ValueError:
            reason = "Text evidence invalid"
        if reason:
            excluded.append({"property_id": prop["id"], "reason": reason})
            continue
        signals = {item["signal"]: item["state"] for item in review.get("text_signals", [])}
        candidates.append({"property_id": prop["id"], "group_id": prop["group_id"],
            "protected": prop["split"] == "test", "label_evidence_id": evidence,
            "review_revision": review.get("revision", 0), "reviewer": review["reviewer"],
            "physical_condition": review.get("physical_condition", "UNKNOWN"),
            "modernization": review.get("modernization_state", "UNKNOWN"),
            "acquisition_fit": {"target":"TARGET", "not_target":"NOT_TARGET"}.get(review.get("target_fit"), "UNKNOWN"),
            "text_signals": {signal: signals.get(signal, "UNKNOWN") for signal in TEXT_SIGNALS},
            "usable_subject_photos": sum(image.get("selection", {}).get("included", True)
                and image.get("effective", {}).get("context", image.get("provider_context")) in {"subject", "subject_interior", "subject_exterior"}
                for image in detail["images"]),
            "has_remarks": bool((current.get("mls_remarks") or "").strip()),
            "has_structured": bool(prop.get("metadata")),
        })
    groups = defaultdict(list)
    for row in candidates:
        groups[row["group_id"]].append(row)
    coverage = {}
    for split in ("train", "validation", "protected_test"):
        coverage[split] = {"physical_condition": dict.fromkeys(PHYSICAL_CONDITIONS, 0),
            "modernization": dict.fromkeys(MODERNIZATION_STATES, 0),
            "acquisition_fit": dict.fromkeys(("TARGET", "NOT_TARGET", "UNKNOWN"), 0),
            "text_signals": {signal: dict.fromkeys(("PRESENT", "ABSENT", "UNKNOWN"), 0) for signal in TEXT_SIGNALS}}
    rows, conflicts = [], []
    for group, members in sorted(groups.items()):
        # Repeated examples never count as independent groups; conflicting truth needs review.
        truths = {digest({k: member[k] for k in ("physical_condition", "modernization", "acquisition_fit", "text_signals")}) for member in members}
        if len(truths) > 1:
            conflicts.append({"group_id": group, "property_ids": [r["property_id"] for r in members]})
            continue
        protected = any(row["protected"] for row in members)
        split = "protected_test" if protected else "validation" if int(digest({"v2_group":group})[:8], 16) / 2**32 < .2 else "train"
        row = {**members[0], "split": split, "member_property_ids": [r["property_id"] for r in members]}
        rows.append(row)
        for axis in ("physical_condition", "modernization", "acquisition_fit"):
            coverage[split][axis][row[axis]] += 1
        for signal, state in row["text_signals"].items():
            coverage[split]["text_signals"][signal][state] += 1
    return {"policy": "approved-human-typed-v2", "fingerprint": digest(sorted(candidates, key=lambda r:r["property_id"])),
        "approved_review_rows": len(candidates), "independent_groups": dict(Counter(row["split"] for row in rows)),
        "coverage": coverage, "examples": rows, "excluded": excluded, "conflicting_groups": conflicts,
        "draft_or_legacy_reviews": drafts, "missing_modalities": {
            "photos": sum(not row["usable_subject_photos"] for row in rows),
            "remarks": sum(not row["has_remarks"] for row in rows),
            "structured": sum(not row["has_structured"] for row in rows)},
        "trainable": False, "training_gate": "Grouped v2 orchestration, calibration and durable bundles remain unprovisioned",
        "notice": "UNKNOWN is retained independently per task. Missing photos do not exclude text/structured evidence. Read-only preview."}

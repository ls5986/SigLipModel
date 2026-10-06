"""Canonical ActVision v2 dataset assembly with explicit per-field provenance."""
from __future__ import annotations

from collections import Counter, defaultdict
from uuid import uuid4

from actvision_contract import digest, remarks_digest
from condition_schema import (
    LABEL_SCHEMA_V2, MODERNIZATION_STATES, PHYSICAL_CONDITIONS, TARGET_LABELS, TEXT_SIGNALS,
)
from structured_model import ALLOWED_FIELDS, CATEGORICAL, NUMERIC, structured_features
from studio_data import now
from typed_label_assistant import POLICY as AI_DRAFT_POLICY

SPLIT_POLICY_VERSION = "actvision-group-split-v2"
LABEL_POLICY_VERSION = "actvision-label-provenance-v2"
PROVENANCE_PRIORITY = {
    "IMPORTED_TARGET": 1,
    "AI_DRAFT": 2,
    "POST_RENOVATION_OUTCOME": 2,
    "PRODUCTION_FEEDBACK": 3,
    "HUMAN_APPROVED": 4,
}
AXES = ("physical_condition", "modernization", "acquisition_fit")


def _structured(metadata):
    """Keep only v2 train/serve fields with exact wire-compatible types."""
    result = {}
    for key in ALLOWED_FIELDS:
        value = (metadata or {}).get(key)
        if value in {None, ""}:
            continue
        if key in NUMERIC:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                continue
        elif key in CATEGORICAL and not isinstance(value, str):
            continue
        result[key] = value
    # Validation catches NaN/Infinity and keeps training identical to serving.
    structured_features(result)
    return result


def _normalize_fit(value):
    return {
        "target": "TARGET", "not_target": "NOT_TARGET", "unsure": "UNKNOWN",
        "TARGET": "TARGET", "NOT_TARGET": "NOT_TARGET", "UNKNOWN": "UNKNOWN",
    }.get(value, "UNKNOWN")


def _labels_from_human(review, remarks, evidence_id):
    if (
        review.get("status") != "approved"
        or review.get("label_schema_version") != LABEL_SCHEMA_V2
        or not review.get("reviewer")
        or review.get("label_evidence_id") != evidence_id
    ):
        return None
    from studio_v2 import validate_text_reviews
    validate_text_reviews(review.get("text_signals", []), remarks)
    physical = review.get("physical_condition", "UNKNOWN")
    modernization = review.get("modernization_state", "UNKNOWN")
    fit = _normalize_fit(review.get("target_fit", review.get("acquisition_fit")))
    if physical not in PHYSICAL_CONDITIONS or modernization not in MODERNIZATION_STATES or fit not in TARGET_LABELS:
        raise ValueError("Invalid approved v2 review")
    signals = {item["signal"]: item["state"] for item in review.get("text_signals", [])}
    return {
        "physical_condition": physical,
        "modernization": modernization,
        "acquisition_fit": fit,
        "text_signals": {name: signals.get(name, "UNKNOWN") for name in TEXT_SIGNALS},
        "source_id": f"review:{review.get('revision', 0)}",
        "reviewer": review["reviewer"],
    }


def _labels_from_draft(store, prop_id, remarks, evidence_id):
    draft = store.document("typed-label-result:" + prop_id) or {}
    if (
        draft.get("status") != "draft"
        or draft.get("policy") != AI_DRAFT_POLICY
        or draft.get("label_schema_version") != LABEL_SCHEMA_V2
        or draft.get("label_evidence_id") != evidence_id
        or not draft.get("proposal_id")
    ):
        return None
    from studio_v2 import validate_text_reviews
    validate_text_reviews(draft.get("text_signals", []), remarks)
    physical = draft.get("physical_condition", "UNKNOWN")
    modernization = draft.get("modernization_state", "UNKNOWN")
    fit = _normalize_fit(draft.get("acquisition_fit", draft.get("target_fit")))
    if physical not in PHYSICAL_CONDITIONS or modernization not in MODERNIZATION_STATES or fit not in TARGET_LABELS:
        raise ValueError("Invalid AI draft labels")
    signals = {item["signal"]: item["state"] for item in draft.get("text_signals", [])}
    return {
        "physical_condition": physical,
        "modernization": modernization,
        "acquisition_fit": fit,
        "text_signals": {name: signals.get(name, "UNKNOWN") for name in TEXT_SIGNALS},
        "source_id": "proposal:" + draft["proposal_id"],
    }


def _reviewed_feedback(store):
    """Latest reviewed production corrections keyed by listing ID."""
    if not hasattr(store, "database"):
        return {}
    with store.database.connect() as db:
        rows = db.execute("""
          SELECT e.event_id,e.payload,d.id AS decision_id,d.created_at
          FROM acq_training.actvision_feedback_events e
          JOIN LATERAL (
            SELECT id,decision,created_at
            FROM acq_training.actvision_feedback_decisions d
            WHERE d.workspace_id=e.workspace_id AND d.event_id=e.event_id
            ORDER BY d.created_at DESC,d.id DESC LIMIT 1
          ) d ON d.decision='reviewed'
          WHERE e.workspace_id=%s
          ORDER BY d.created_at,e.event_id
        """, (store.workspace,)).fetchall()
    grouped = defaultdict(list)
    for row in rows:
        listing = str((row["payload"].get("evidence") or {}).get("listing_id") or "")
        if listing:
            grouped[listing].append(dict(row))
    return grouped


def _feedback_labels(events, remarks, structured, current_photos):
    """Accept corrections only when their original evidence still matches current evidence."""
    best = {}
    current_hashes = {
        str(photo.get("provider_media_key") or photo.get("id", "").split(":")[-1]): photo.get("sha256")
        for photo in current_photos
    }
    for row in events:
        payload = row["payload"]
        evidence = payload.get("evidence") or {}
        if evidence.get("remarks_sha256") != remarks_digest(remarks):
            continue
        if evidence.get("structured_sha256") != digest(structured):
            continue
        changed = False
        for photo in evidence.get("selected_photos", []):
            known = photo.get("sha256")
            if known is not None and current_hashes.get(str(photo.get("photo_id"))) != known:
                changed = True
                break
        if changed:
            continue
        for correction in payload.get("corrections", []):
            kind = correction.get("type")
            if kind == "physical_condition":
                best["physical_condition"] = correction.get("value")
            elif kind == "modernization":
                best["modernization"] = correction.get("value")
            elif kind == "acquisition_fit":
                best["acquisition_fit"] = correction.get("value")
            elif kind == "text_signal":
                value = correction.get("value") or {}
                signal = value.get("signal")
                if signal in TEXT_SIGNALS:
                    best.setdefault("text_signals", {})[signal] = value.get("state", "UNKNOWN")
        best["source_id"] = "feedback:" + row["event_id"]
    return best or None


def _apply(target, provenance, candidate, origin):
    """Fill known fields only when this origin outranks the previous origin."""
    if not candidate:
        return
    priority = PROVENANCE_PRIORITY[origin]
    for axis in AXES:
        value = candidate.get(axis, "UNKNOWN")
        if value == "UNKNOWN":
            continue
        previous = provenance.get(axis)
        if previous is None or priority > previous["priority"]:
            target[axis] = value
            provenance[axis] = {"origin": origin, "priority": priority, "source_id": candidate.get("source_id")}
    for signal, value in (candidate.get("text_signals") or {}).items():
        if value == "UNKNOWN":
            continue
        key = "text_signal:" + signal
        previous = provenance.get(key)
        if previous is None or priority > previous["priority"]:
            target["text_signals"][signal] = value
            provenance[key] = {"origin": origin, "priority": priority, "source_id": candidate.get("source_id")}


def _split(group_id, protected):
    if protected:
        return "test"
    return "validation" if int(digest({"actvision_v2_group": group_id})[:8], 16) / 2**32 < .2 else "train"



def _after_event_examples(store, acquisition_properties):
    """Return later-sale NOT_TARGET events without exposing outcome economics as inputs."""
    by_example = {
        str(prop["example_id"]): prop for prop in acquisition_properties
    }
    if not by_example:
        return []
    with store.database.connect() as db:
        rows = db.execute("""
          SELECT e.id,e.group_id,e.source_snapshot,
                 c.item AS candidate,
                 p.id AS photo_uuid,p.provider_media_key,p.image_sha256,
                 p.storage_bucket,p.storage_object_key,p.context,p.context_evidence
          FROM acq_training.examples e
          JOIN LATERAL (
            SELECT item
            FROM jsonb_array_elements(e.source_snapshot->'mls_candidates') item
            WHERE item->'listing'->>'ListingKey'=e.source_snapshot->'event_map'->>'after_listing_key'
            LIMIT 1
          ) c ON true
          LEFT JOIN acq_training.photos p
            ON (p.workspace_id,p.example_id)=(e.workspace_id,e.id)
           AND p.revoked_at IS NULL
           AND (p.retention_until IS NULL OR p.retention_until>now())
           AND p.context_evidence->>'event_role'='after'
          WHERE e.workspace_id=%s
            AND e.source_snapshot->'event_map'->>'recovery_status'='mapped'
            AND nullif(e.source_snapshot->'event_map'->>'after_listing_key','') IS NOT NULL
          ORDER BY e.id,p.provider_media_key,p.id
        """, (store.workspace,)).fetchall()

    grouped = defaultdict(lambda: {"photos": []})
    for row in rows:
        example_id = str(row["id"])
        base = by_example.get(example_id)
        if not base:
            continue
        item = grouped[example_id]
        item.update({
            "example_id": example_id,
            "source_group_id": str(row["group_id"]),
            "split_group_id": str(base["group_id"]),
            "split": base["split"],
            "candidate": row["candidate"],
        })
        if row["photo_uuid"] is not None:
            context = row["context"] or "unknown"
            if context not in {"shared_amenity", "floor_plan", "unrelated"}:
                item["photos"].append({
                    "photo_id": str(row["candidate"]["listing"]["ListingKey"]) + ":" + str(row["provider_media_key"]),
                    "sha256": row["image_sha256"],
                    "storage_bucket": row["storage_bucket"],
                    "storage_object_key": row["storage_object_key"],
                    "room": "other",
                })

    output = []
    for item in grouped.values():
        listing = item["candidate"].get("listing", {})
        property_id = str(listing.get("ListingKey") or "")
        if not property_id:
            continue
        remarks = __import__("listing_text").remarks(listing)
        structured = _structured(listing)
        available = []
        if item["photos"]:
            available.append("vision")
        if remarks.strip():
            available.append("text")
        if structured:
            available.append("structured")
        if not available:
            continue
        output.append({
            "property_id": property_id,
            "example_id": item["example_id"],
            "source_group_id": item["source_group_id"],
            "split_group_id": item["split_group_id"],
            "split": item["split"],
            "event_role": "after",
            "evidence_id": digest({
                "property_id": property_id,
                "remarks_sha256": remarks_digest(remarks),
                "structured": structured,
                "photo_hashes": [photo["sha256"] for photo in item["photos"]],
            }),
            "labels": {
                "physical_condition": "UNKNOWN",
                "modernization": "UNKNOWN",
                "acquisition_fit": "NOT_TARGET",
                "text_signals": {name: "UNKNOWN" for name in TEXT_SIGNALS},
            },
            "provenance": {
                "acquisition_fit": {
                    "origin": "POST_RENOVATION_OUTCOME",
                    "source_id": "event-map:" + item["example_id"],
                },
            },
            "remarks": remarks if "text" in available else "",
            "structured": structured if "structured" in available else {},
            "photos": item["photos"],
            "available_modalities": available,
        })
    return output

def build(store):
    """Create a deterministic, leakage-safe v2 manifest without writing it."""
    from cloud_training import snapshot
    from studio_v2 import label_evidence

    photo_rows, properties = snapshot(store, include_legacy=False)
    photos_by_property = defaultdict(list)
    for photo in photo_rows:
        photos_by_property[str(photo["property_id"])].append(photo)
    feedback_by_listing = _reviewed_feedback(store)

    rows, exclusions = [], Counter()
    for prop in properties:
        prop_id = str(prop["id"])
        detail = store.property(prop_id)
        current = detail["property"]
        history = detail.get("historical_source") or {}
        if history.get("blocked"):
            exclusions["source_or_era_quarantined"] += 1
            continue

        remarks = current.get("mls_remarks") or ""
        structured = _structured(current.get("metadata") or {})
        evidence_id = label_evidence(prop_id, remarks, detail["images"], current.get("metadata"))
        labels = {
            "physical_condition": "UNKNOWN",
            "modernization": "UNKNOWN",
            "acquisition_fit": "UNKNOWN",
            "text_signals": {name: "UNKNOWN" for name in TEXT_SIGNALS},
        }
        provenance = {}

        # Imported target establishes fit only. It is never physical-condition truth.
        if prop.get("known_target"):
            _apply(labels, provenance, {
                "acquisition_fit": "TARGET", "source_id": "imported:" + str(prop.get("target_origin") or "target")
            }, "IMPORTED_TARGET")

        draft = _labels_from_draft(store, prop_id, remarks, evidence_id)
        _apply(labels, provenance, draft, "AI_DRAFT")

        feedback = _feedback_labels(
            feedback_by_listing.get(prop_id, []), remarks, structured, detail["images"]
        )
        _apply(labels, provenance, feedback, "PRODUCTION_FEEDBACK")

        human = _labels_from_human(current.get("review") or {}, remarks, evidence_id)
        _apply(labels, provenance, human, "HUMAN_APPROVED")

        if all(labels[axis] == "UNKNOWN" for axis in AXES) and all(
            value == "UNKNOWN" for value in labels["text_signals"].values()
        ):
            exclusions["no_supported_labels"] += 1
            continue

        source_photos = {row["id"]: row for row in photos_by_property.get(prop_id, [])}
        vision = []
        if prop.get("timing_verified"):
            for image in detail["images"]:
                source = source_photos.get(image["id"])
                if not source or source.get("label_exclusion") or not source.get("include_in_similarity"):
                    continue
                context = image.get("effective", {}).get("context", image.get("provider_context"))
                if context not in {"subject", "subject_interior", "subject_exterior"}:
                    continue
                if image.get("synthetic_evidence", {}).get("excluded"):
                    continue
                vision.append({
                    "photo_id": image["id"],
                    "sha256": image["sha256"],
                    "storage_bucket": source["storage_bucket"],
                    "storage_object_key": source["storage_object_key"],
                    "room": image.get("effective", {}).get("room") or "other",
                })

        available = []
        if vision:
            available.append("vision")
        if prop.get("text_source_valid") and remarks.strip():
            available.append("text")
        if prop.get("text_source_valid") and structured:
            available.append("structured")
        if not available:
            exclusions["no_usable_modality"] += 1
            continue

        split_group = str(prop["group_id"])
        split = _split(split_group, prop.get("split") == "test")
        rows.append({
            "property_id": prop_id,
            "event_role": "acquisition",
            "example_id": str(prop["example_id"]),
            "source_group_id": str(prop["source_group_id"]),
            "split_group_id": split_group,
            "split": split,
            "evidence_id": evidence_id,
            "labels": labels,
            "provenance": {key: {k: v for k, v in value.items() if k != "priority"}
                           for key, value in provenance.items()},
            "remarks": remarks if "text" in available else "",
            "structured": structured if "structured" in available else {},
            "photos": vision,
            "available_modalities": available,
        })

    # Later renovated/resale listings are valid NOT_TARGET examples but remain
    # in the exact same physical-property split as their acquisition event.
    rows.extend(_after_event_examples(store, properties))

    # Collapse duplicate aliases within an event, not across different time
    # states of the same physical property.
    grouped_rows = defaultdict(list)
    for row in rows:
        grouped_rows[(row["split_group_id"], row.get("event_role", "acquisition"))].append(row)
    canonical_rows = []
    split_sets = defaultdict(set)
    for (split_group, event_role), members in sorted(grouped_rows.items()):
        splits = {row["split"] for row in members}
        split_sets[split_group].update(splits)
        if len(splits) != 1:
            raise ValueError("Merged physical group event leaked across dataset splits")
        # Conflicting human-approved values are never auto-resolved.
        conflict = False
        for axis in AXES:
            human_values = {
                row["labels"][axis] for row in members
                if row["labels"][axis] != "UNKNOWN"
                and (row["provenance"].get(axis) or {}).get("origin") == "HUMAN_APPROVED"
            }
            if len(human_values) > 1:
                conflict = True
        if conflict:
            exclusions["conflicting_human_group"] += 1
            continue
        def rank(row):
            provenance_strength = sum(
                PROVENANCE_PRIORITY.get(value.get("origin"), 0)
                for value in row["provenance"].values()
            )
            return (
                provenance_strength,
                len(row["available_modalities"]),
                len(row["photos"]),
                bool(row["remarks"]),
                len(row["structured"]),
                row["property_id"],
            )
        canonical_rows.append(max(members, key=rank))

    if any(len(splits) != 1 for splits in split_sets.values()):
        raise ValueError("Physical property events leaked across dataset splits")
    split_by_group = {group: splits for group, splits in split_sets.items()}
    canonical_rows = sorted(
        canonical_rows,
        key=lambda row: (row["split_group_id"], row.get("event_role", "acquisition"), row["property_id"]),
    )
    fingerprint = digest({
        "policy": LABEL_POLICY_VERSION,
        "split_policy": SPLIT_POLICY_VERSION,
        "rows": canonical_rows,
    })
    origins = Counter(
        source["origin"] for row in canonical_rows for source in row["provenance"].values()
    )
    return {
        "policy": LABEL_POLICY_VERSION,
        "split_policy": SPLIT_POLICY_VERSION,
        "fingerprint": fingerprint,
        "rows": canonical_rows,
        "counts": {
            "properties": len(canonical_rows),
            "groups": len(split_by_group),
            "acquisition_events": sum(row.get("event_role") == "acquisition" for row in canonical_rows),
            "after_events": sum(row.get("event_role") == "after" for row in canonical_rows),
            "splits": dict(Counter(row["split"] for row in canonical_rows)),
            "origins": dict(origins),
            "vision": sum("vision" in row["available_modalities"] for row in canonical_rows),
            "text": sum("text" in row["available_modalities"] for row in canonical_rows),
            "structured": sum("structured" in row["available_modalities"] for row in canonical_rows),
            "excluded": dict(exclusions),
        },
    }


def preview(store):
    manifest = build(store)
    coverage = {}
    for split in ("train", "validation", "test"):
        selected = [row for row in manifest["rows"] if row["split"] == split]
        coverage[split] = {
            axis: dict(Counter(row["labels"][axis] for row in selected))
            for axis in AXES
        }
    return {
        **{key: manifest[key] for key in ("policy", "split_policy", "fingerprint", "counts")},
        "coverage": coverage,
        "trainable": _trainable(manifest),
        "notice": "AI drafts remain unreviewed provenance. Human-approved and reviewed production corrections override lower-priority labels field by field.",
    }


def _trainable(manifest):
    train = [row for row in manifest["rows"] if row["split"] == "train"]
    if len({row["split_group_id"] for row in train}) < 20:
        return False
    return any(len({row["labels"][axis] for row in train if row["labels"][axis] != "UNKNOWN"}) >= 2
               for axis in AXES)


def freeze(store, payload):
    if payload.get("confirmed") is not True:
        raise ValueError("Confirm freezing the current v2 dataset")
    manifest = build(store)
    if not _trainable(manifest):
        raise ValueError("Current v2 labels do not support a supervised candidate yet")
    identifier = str(uuid4())
    groups = []
    # Persisted source groups may have aliases; all aliases inherit the merged split.
    seen_groups = set()
    for row in manifest["rows"]:
        key = row["source_group_id"]
        if key not in seen_groups:
            groups.append({"group_id": key, "split": row["split"]})
            seen_groups.add(key)
    items = [{
        "group_id": row["source_group_id"],
        "example_id": row["example_id"],
        "label_snapshot": {
            "property_id": row["property_id"],
            "split_group_id": row["split_group_id"],
            "evidence_id": row["evidence_id"],
            "labels": row["labels"],
            "provenance": row["provenance"],
            "remarks": row["remarks"],
            "structured": row["structured"],
            "photos": row["photos"],
            "available_modalities": row["available_modalities"],
        },
        "photo_hashes": [photo["sha256"] for photo in row["photos"]],
        "source_review_ids": [],
    } for row in manifest["rows"]]

    from psycopg.types.json import Jsonb
    with store.database.connect() as db:
        version = db.execute(
            "SELECT coalesce(max(version),0)+1 AS version FROM acq_training.datasets "
            "WHERE workspace_id=%s AND name='actvision-v2'",
            (store.workspace,),
        ).fetchone()["version"]
        db.execute(
            "SELECT acq_training.create_frozen_dataset_v2(%s,%s,%s,%s,%s,%s,%s,%s)",
            (store.workspace, identifier, "actvision-v2", version, manifest["fingerprint"],
             Jsonb({"policy": LABEL_POLICY_VERSION, "split_policy": SPLIT_POLICY_VERSION}),
             Jsonb(groups), Jsonb(items)),
        )
    saved = store.document("actvision-v2-dataset-latest") or {}
    store.save_document("actvision-v2-dataset-latest", {
        "id": identifier, "version": version, "fingerprint": manifest["fingerprint"],
        "counts": manifest["counts"], "frozen_at": now(),
    }, saved.get("revision", 0))
    return {
        "id": identifier, "version": version, "fingerprint": manifest["fingerprint"],
        "counts": manifest["counts"], "frozen": True,
    }


def load_frozen(store, dataset_id):
    with store.database.connect() as db:
        dataset = db.execute("""
          SELECT id,version,manifest_sha256,label_policy,frozen_at
          FROM acq_training.datasets
          WHERE workspace_id=%s AND id=%s AND frozen_at IS NOT NULL
        """, (store.workspace, dataset_id)).fetchone()
        if not dataset:
            raise ValueError("Frozen v2 dataset not found")
        rows = db.execute("""
          SELECT g.split,i.example_id,i.group_id,i.label_snapshot,i.photo_hashes
          FROM acq_training.dataset_items i
          JOIN acq_training.dataset_groups g
            ON (g.workspace_id,g.dataset_id,g.group_id)=(i.workspace_id,i.dataset_id,i.group_id)
          WHERE i.workspace_id=%s AND i.dataset_id=%s
          ORDER BY i.example_id
        """, (store.workspace, dataset_id)).fetchall()
    values = []
    for row in rows:
        snap = row["label_snapshot"]
        values.append({
            **snap, "split": row["split"], "example_id": str(row["example_id"]),
            "source_group_id": str(row["group_id"]),
        })
    recomputed = digest({
        "policy": LABEL_POLICY_VERSION,
        "split_policy": SPLIT_POLICY_VERSION,
        "rows": sorted(values, key=lambda row: (row["split_group_id"], row["property_id"])),
    })
    if recomputed != dataset["manifest_sha256"]:
        raise ValueError("Frozen dataset fingerprint no longer matches materialized rows")
    return {"dataset": dict(dataset), "rows": values}

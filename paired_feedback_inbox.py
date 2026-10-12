"""Immutable review and approval boundary for paired-condition MLS feedback."""
from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
from typing import Any, Literal


Relation = Literal["current", "repeated_review", "prior_prediction"]
ReviewStatus = Literal["pending", "held", "approved"]


@dataclass(frozen=True)
class Approval:
    event_id: str
    event_sha256: str
    actor: str
    usage: Literal["challenge", "training_candidate"]
    axis_eligibility: dict[str, str]
    approved_at: str


@dataclass(frozen=True)
class FeedbackReview:
    event_id: str
    event_sha256: str
    listing_key: str
    listing_id: str | None
    physical_property_id: str | None
    prediction_id: str
    evidence_id: str
    release_id: str
    received_at: str
    reviewed_at: str
    relation: Relation
    status: ReviewStatus
    event: dict[str, Any]
    approval: Approval | None


def _digest(value: object) -> str:
    encoded = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode()
    return hashlib.sha256(encoded).hexdigest()


def _documents(store: Any, prefix: str) -> list[tuple[str, dict[str, Any]]]:
    in_memory = getattr(store, "documents", None)
    if isinstance(in_memory, dict):
        return [
            (key, deepcopy(value))
            for key, value in in_memory.items()
            if key.startswith(prefix)
        ]
    with store.database.connect() as db:
        rows = db.execute(
            """SELECT item_id,payload,revision
               FROM acq_training.studio_state
               WHERE workspace_id=%s AND kind='document' AND item_id LIKE %s
               ORDER BY item_id""",
            (store.workspace, prefix + "%"),
        ).fetchall()
    return [
        (
            str(row["item_id"]),
            {**deepcopy(row["payload"]), "revision": row["revision"]},
        )
        for row in rows
    ]


def _timestamp(value: object, field: str) -> str:
    if not isinstance(value, str):
        raise ValueError(f"Feedback {field} is missing")
    try:
        datetime.fromisoformat(value)
    except ValueError:
        raise ValueError(f"Feedback {field} is invalid") from None
    return value


def _approval(store: Any, event_id: str, event_sha256: str) -> Approval | None:
    saved = store.document("paired-condition-feedback-approval:" + event_id)
    if not saved:
        return None
    if saved.get("event_id") != event_id or saved.get("event_sha256") != event_sha256:
        raise ValueError("Feedback approval identity mismatch")
    return Approval(
        event_id=event_id,
        event_sha256=event_sha256,
        actor=str(saved.get("actor") or ""),
        usage=saved["usage"],
        axis_eligibility=deepcopy(saved.get("axis_eligibility") or {}),
        approved_at=_timestamp(saved.get("approved_at"), "approval timestamp"),
    )


def list_feedback(store: Any, release_id: str) -> list[FeedbackReview]:
    normalized = []
    prefix = "paired-condition-feedback-inbox:"
    for key, saved in _documents(store, prefix):
        event = saved.get("event")
        if not isinstance(event, dict):
            raise ValueError("Feedback inbox event is missing")
        event_id = str(event.get("event_id") or "")
        if not event_id or key != prefix + event_id:
            raise ValueError("Feedback inbox event identity mismatch")
        event_sha256 = str(saved.get("event_sha256") or "")
        if event_sha256 != _digest(event):
            raise ValueError("Feedback inbox digest mismatch")
        release = event.get("release")
        if not isinstance(release, dict):
            raise ValueError("Feedback release identity is missing")
        event_release_id = str(release.get("release_id") or "")
        if event_release_id != release_id:
            continue
        scored_input = event.get("scored_input")
        request = scored_input.get("request") if isinstance(scored_input, dict) else None
        listing = request.get("listing") if isinstance(request, dict) else None
        if not isinstance(listing, dict) or not listing.get("listing_key"):
            raise ValueError("Feedback listing identity is missing")
        normalized.append(
            {
                "event_id": event_id,
                "event_sha256": event_sha256,
                "listing_key": str(listing["listing_key"]),
                "listing_id": (
                    str(listing["listing_id"]) if listing.get("listing_id") else None
                ),
                "physical_property_id": (
                    str(listing["physical_property_id"])
                    if listing.get("physical_property_id")
                    else None
                ),
                "prediction_id": str(event.get("prediction_id") or ""),
                "evidence_id": str(event.get("evidence_id") or ""),
                "release_id": event_release_id,
                "received_at": _timestamp(saved.get("received_at"), "received_at"),
                "reviewed_at": _timestamp(event.get("reviewed_at"), "reviewed_at"),
                "event": deepcopy(event),
            }
        )

    current_by_listing = {}
    for item in normalized:
        current = current_by_listing.get(item["listing_key"])
        if current is None or (item["reviewed_at"], item["event_id"]) > (
            current["reviewed_at"],
            current["event_id"],
        ):
            current_by_listing[item["listing_key"]] = item

    reviews = []
    for item in normalized:
        current = current_by_listing[item["listing_key"]]
        if item["event_id"] == current["event_id"]:
            relation: Relation = "current"
        elif item["prediction_id"] == current["prediction_id"]:
            relation = "repeated_review"
        else:
            relation = "prior_prediction"
        approval = _approval(store, item["event_id"], item["event_sha256"])
        status: ReviewStatus = (
            "pending"
            if approval is None
            else "held"
            if approval.usage == "challenge"
            else "approved"
        )
        reviews.append(
            FeedbackReview(
                **item,
                relation=relation,
                status=status,
                approval=approval,
            )
        )
    return sorted(
        reviews,
        key=lambda item: (item.reviewed_at, item.event_id),
        reverse=True,
    )


def _approval_request(
    event: dict[str, Any],
    event_id: str,
    event_sha256: str,
    actor: str,
    usage: str,
    axis_eligibility: dict[str, str] | None,
) -> dict[str, Any]:
    from paired_condition_service import (
        CANDIDATE_SHA256,
        MANIFEST_SHA256,
        POLICY_VERSION,
        RELEASE_ID,
    )

    if not actor.strip():
        raise ValueError("Approval actor is required")
    if usage not in {"challenge", "training_candidate"}:
        raise ValueError("Approval usage must be challenge or training_candidate")
    axes = ("image", "metadata", "overall")
    if (
        not isinstance(axis_eligibility, dict)
        or set(axis_eligibility) != set(axes)
        or any(axis_eligibility[axis] not in {"eligible", "excluded"} for axis in axes)
    ):
        raise ValueError("An axis eligibility decision is required for every human label")

    release = event.get("release")
    expected_release = {
        "release_id": RELEASE_ID,
        "manifest_sha256": MANIFEST_SHA256,
        "candidate_sha256": CANDIDATE_SHA256,
        "evidence_policy_version": POLICY_VERSION,
    }
    if not isinstance(release, dict) or any(
        release.get(key) != value for key, value in expected_release.items()
    ):
        raise ValueError("Feedback release identity does not match the pinned paired release")

    scored_input = event.get("scored_input")
    request = scored_input.get("request") if isinstance(scored_input, dict) else None
    evidence = request.get("evidence") if isinstance(request, dict) else None
    listing = request.get("listing") if isinstance(request, dict) else None
    if (
        not isinstance(evidence, dict)
        or not isinstance(listing, dict)
        or not listing.get("listing_key")
        or request.get("evidence_id") != event.get("evidence_id")
    ):
        raise ValueError("Feedback evidence identity is missing or inconsistent")
    photos = evidence.get("photos")
    if not isinstance(photos, list):
        raise ValueError("Feedback photo evidence is unknown")
    photo_ids = {
        str(photo["photo_id"])
        for photo in photos
        if isinstance(photo, dict) and photo.get("photo_id")
    }
    for correction in event.get("corrected_photos", []):
        if not isinstance(correction, dict) or str(correction.get("photo_id") or "") not in photo_ids:
            raise ValueError("Corrected photo references unknown evidence")
        if correction.get("excluded") and not str(
            correction.get("exclusion_reason") or ""
        ).strip():
            raise ValueError("Excluded photos require an exclusion reason")

    labels = event.get("human_labels")
    if not isinstance(labels, dict):
        raise ValueError("Human labels are missing")
    if usage == "training_candidate":
        for axis in axes:
            label = labels.get(axis)
            decision = label.get("decision") if isinstance(label, dict) else None
            if (
                axis_eligibility[axis] == "eligible"
                and decision == "INSUFFICIENT_EVIDENCE"
            ):
                raise ValueError(
                    "Insufficient evidence cannot become binary training truth"
                )
        if axis_eligibility["image"] == "eligible" and not photo_ids:
            raise ValueError("Image training eligibility requires known photo evidence")

    return {
        "schema_version": "paired-condition-feedback-approval-v1",
        "event_id": event_id,
        "event_sha256": event_sha256,
        "actor": actor.strip(),
        "usage": usage,
        "axis_eligibility": deepcopy(axis_eligibility),
    }


def approve_feedback(
    store: Any,
    event_id: str,
    event_sha256: str,
    actor: str,
    usage: str,
    *,
    axis_eligibility: dict[str, str] | None = None,
) -> Approval:
    inbox_key = "paired-condition-feedback-inbox:" + event_id
    saved = store.document(inbox_key)
    if not saved or not isinstance(saved.get("event"), dict):
        raise ValueError("Feedback event was not found")
    event = saved["event"]
    stored_sha256 = str(saved.get("event_sha256") or "")
    if (
        event.get("event_id") != event_id
        or stored_sha256 != event_sha256
        or stored_sha256 != _digest(event)
    ):
        raise ValueError("Feedback event digest does not match")

    requested = _approval_request(
        event,
        event_id,
        event_sha256,
        actor,
        usage,
        axis_eligibility,
    )
    key = "paired-condition-feedback-approval:" + event_id
    existing = store.document(key)
    if existing:
        comparable = {field: existing.get(field) for field in requested}
        if comparable != requested:
            raise RuntimeError("Feedback event already has a different approval")
        approval = _approval(store, event_id, event_sha256)
        if approval is None:
            raise RuntimeError("Feedback approval could not be read")
        return approval

    payload = {
        **requested,
        "approved_at": datetime.now(timezone.utc).isoformat(),
    }
    try:
        store.save_document(key, payload, 0)
    except (RuntimeError, ValueError):
        concurrent = store.document(key)
        comparable = (
            {field: concurrent.get(field) for field in requested}
            if isinstance(concurrent, dict)
            else {}
        )
        if comparable != requested:
            raise RuntimeError(
                "Feedback event already has a different approval"
            ) from None
    approval = _approval(store, event_id, event_sha256)
    if approval is None:
        raise RuntimeError("Feedback approval was not saved")
    return approval


def feedback_dict(review: FeedbackReview) -> dict[str, Any]:
    return asdict(review)

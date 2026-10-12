from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from paired_condition_service import (
    CANDIDATE_SHA256,
    MANIFEST_SHA256,
    POLICY_VERSION,
    RELEASE_ID,
    digest,
)
from paired_feedback_inbox import approve_feedback, list_feedback


class Store:
    def __init__(self):
        self.documents = {}

    def document(self, key):
        return deepcopy(self.documents.get(key))

    def save_document(self, key, payload, expected_revision):
        if key in self.documents or expected_revision != 0:
            raise RuntimeError("Document revision conflict")
        saved = {**deepcopy(payload), "revision": 1}
        self.documents[key] = saved
        return deepcopy(saved)


def decision(value="NOT_TARGET"):
    return {"decision": value, "strength": None, "affinity": 0.1}


def feedback_event(index, *, prediction_id=None, reviewed_at=None):
    listing_key = f"listing-{index}"
    prediction_id = prediction_id or str(uuid4())
    event_id = str(uuid4())
    reviewed_at = reviewed_at or datetime.now(timezone.utc)
    return {
        "schema_version": "actvision-paired-condition-v1",
        "event_id": event_id,
        "idempotency_key": event_id,
        "workspace_id": "mls-development",
        "prediction_id": prediction_id,
        "request_id": str(uuid4()),
        "evidence_id": f"{index + 1:064x}",
        "release": {
            "release_id": RELEASE_ID,
            "candidate_id": "candidate",
            "dataset_id": "dataset-v5",
            "dataset_version": 5,
            "manifest_sha256": MANIFEST_SHA256,
            "candidate_sha256": CANDIDATE_SHA256,
            "evidence_policy_version": POLICY_VERSION,
            "training_code_commit": "c" * 40,
            "encoders": {
                "vision_revision": "vision",
                "text_checkpoint_sha256": "d" * 64,
            },
        },
        "scored_input": {
            "request": {
                "listing": {
                    "provider": "trestle",
                    "listing_key": listing_key,
                    "listing_id": f"MLS-{index}",
                    "physical_property_id": f"property-{index}",
                },
                "evidence_id": f"{index + 1:064x}",
                "evidence": {"photos": []},
            },
            "learned_prediction": {
                axis: decision() for axis in ("image", "remarks", "structured", "metadata", "overall")
            },
            "displayed_decisions": {
                axis: decision() for axis in ("image", "remarks", "structured", "metadata", "overall")
            },
            "evidence_policy": {"version": "remarks-condition-evidence-v3"},
            "coverage": {
                "selected_photo_count": 0,
                "usable_photo_count": 0,
                "missing_photo_count": 0,
            },
            "modality_errors": [],
        },
        "human_labels": {
            "image": decision("INSUFFICIENT_EVIDENCE"),
            "metadata": decision(),
            "overall": decision(),
        },
        "corrected_photos": [],
        "supporting_remark_quotes": [],
        "notes": "",
        "reviewer_id": "reviewer@example.test",
        "reviewed_at": reviewed_at.isoformat(),
    }


def save_inbox(store, event, received_at=None):
    store.documents[f"paired-condition-feedback-inbox:{event['event_id']}"] = {
        "status": "pending",
        "event_sha256": digest(event),
        "event": deepcopy(event),
        "received_at": (received_at or datetime.now(timezone.utc)).isoformat(),
        "training_approved": False,
        "revision": 1,
    }


def current_inbox():
    store = Store()
    now = datetime(2026, 10, 12, tzinfo=timezone.utc)
    base = []
    for index in range(21):
        event = feedback_event(index, reviewed_at=now + timedelta(minutes=index))
        base.append(event)
        save_inbox(store, event)

    for index in (0, 1):
        repeated = feedback_event(
            index,
            prediction_id=base[index]["prediction_id"],
            reviewed_at=now + timedelta(hours=1, minutes=index),
        )
        repeated["evidence_id"] = base[index]["evidence_id"]
        repeated["scored_input"]["request"]["evidence_id"] = base[index]["evidence_id"]
        save_inbox(store, repeated)

    prior = feedback_event(
        2,
        prediction_id=str(uuid4()),
        reviewed_at=now - timedelta(hours=1),
    )
    prior["evidence_id"] = "f" * 64
    prior["scored_input"]["request"]["evidence_id"] = prior["evidence_id"]
    save_inbox(store, prior)
    return store


def test_lists_24_events_as_21_current_reviews_with_history_preserved():
    reviews = list_feedback(current_inbox(), RELEASE_ID)

    assert len(reviews) == 24
    assert sum(review.relation == "current" for review in reviews) == 21
    assert sum(review.relation == "repeated_review" for review in reviews) == 2
    assert sum(review.relation == "prior_prediction" for review in reviews) == 1
    assert all(review.status == "pending" for review in reviews)
    assert all(review.approval is None for review in reviews)
    assert len({review.event_id for review in reviews}) == 24


def test_rejects_an_inbox_document_whose_saved_digest_no_longer_matches():
    store = current_inbox()
    key = next(iter(store.documents))
    store.documents[key]["event_sha256"] = "0" * 64

    with pytest.raises(ValueError, match="digest"):
        list_feedback(store, RELEASE_ID)


def one_event_store():
    store = Store()
    event = feedback_event(0)
    event["scored_input"]["request"]["evidence"] = {
        "photos": [{"photo_id": "photo-1"}],
    }
    save_inbox(store, event)
    return store, event


def eligibility(image="excluded", metadata="eligible", overall="eligible"):
    return {"image": image, "metadata": metadata, "overall": overall}


def test_approval_is_immutable_idempotent_and_does_not_mutate_the_inbox_event():
    store, event = one_event_store()
    key = f"paired-condition-feedback-inbox:{event['event_id']}"
    before = deepcopy(store.documents[key])

    approved = approve_feedback(
        store,
        event["event_id"],
        digest(event),
        "approver@example.test",
        "training_candidate",
        axis_eligibility=eligibility(),
    )

    assert approved.usage == "training_candidate"
    assert store.documents[key] == before
    assert approve_feedback(
        store,
        event["event_id"],
        digest(event),
        "approver@example.test",
        "training_candidate",
        axis_eligibility=eligibility(),
    ) == approved
    with pytest.raises(RuntimeError, match="different approval"):
        approve_feedback(
            store,
            event["event_id"],
            digest(event),
            "other-approver@example.test",
            "training_candidate",
            axis_eligibility=eligibility(),
        )


@pytest.mark.parametrize(
    ("actor", "usage", "axes", "message"),
    [
        ("", "challenge", eligibility(), "actor"),
        ("approver", "", eligibility(), "usage"),
        ("approver", "training_candidate", None, "axis eligibility"),
        (
            "approver",
            "training_candidate",
            {"image": "excluded", "metadata": "eligible"},
            "axis eligibility",
        ),
    ],
)
def test_approval_requires_actor_usage_and_each_axis_decision(actor, usage, axes, message):
    store, event = one_event_store()

    with pytest.raises(ValueError, match=message):
        approve_feedback(
            store,
            event["event_id"],
            digest(event),
            actor,
            usage,
            axis_eligibility=axes,
        )


def test_training_approval_rejects_missing_exclusion_reasons_and_binary_abstentions():
    store, event = one_event_store()
    event["corrected_photos"] = [
        {"photo_id": "photo-1", "excluded": True, "exclusion_reason": ""},
    ]
    save_key = f"paired-condition-feedback-inbox:{event['event_id']}"
    store.documents[save_key]["event"] = deepcopy(event)
    store.documents[save_key]["event_sha256"] = digest(event)
    with pytest.raises(ValueError, match="exclusion reason"):
        approve_feedback(
            store,
            event["event_id"],
            digest(event),
            "approver",
            "training_candidate",
            axis_eligibility=eligibility(),
        )

    store, event = one_event_store()
    event["human_labels"]["metadata"] = decision("INSUFFICIENT_EVIDENCE")
    save_key = f"paired-condition-feedback-inbox:{event['event_id']}"
    store.documents[save_key]["event"] = deepcopy(event)
    store.documents[save_key]["event_sha256"] = digest(event)
    with pytest.raises(ValueError, match="(?i)insufficient evidence"):
        approve_feedback(
            store,
            event["event_id"],
            digest(event),
            "approver",
            "training_candidate",
            axis_eligibility=eligibility(),
        )


def test_approval_rejects_release_mismatch_unknown_evidence_and_digest_mismatch():
    store, event = one_event_store()
    with pytest.raises(ValueError, match="digest"):
        approve_feedback(
            store,
            event["event_id"],
            "0" * 64,
            "approver",
            "challenge",
            axis_eligibility=eligibility("eligible"),
        )

    store, event = one_event_store()
    event["release"]["release_id"] = "different-release"
    key = f"paired-condition-feedback-inbox:{event['event_id']}"
    store.documents[key]["event"] = deepcopy(event)
    store.documents[key]["event_sha256"] = digest(event)
    with pytest.raises(ValueError, match="release"):
        approve_feedback(
            store,
            event["event_id"],
            digest(event),
            "approver",
            "challenge",
            axis_eligibility=eligibility("eligible"),
        )

    store, event = one_event_store()
    del event["scored_input"]["request"]["evidence"]
    key = f"paired-condition-feedback-inbox:{event['event_id']}"
    store.documents[key]["event"] = deepcopy(event)
    store.documents[key]["event_sha256"] = digest(event)
    with pytest.raises(ValueError, match="evidence"):
        approve_feedback(
            store,
            event["event_id"],
            digest(event),
            "approver",
            "challenge",
            axis_eligibility=eligibility("eligible"),
        )

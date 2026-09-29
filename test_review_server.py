import json
import threading
from urllib.error import HTTPError
from urllib.request import Request, urlopen

import pytest

from review_server import ReviewStore, create_server


def payload(**changes):
    return {
        "kind": "image", "id": "train-image", "expected_revision": 0,
        "reviewer": "Test reviewer", "notes": "", "status": "approved",
        "room": "kitchen", "features": {"old_cabinetry": 1, "old_flooring": None},
        **changes,
    }


def test_reviews_persist_separately_and_preserve_unknowns(tmp_path) -> None:
    path = tmp_path / "reviews.json"
    store = ReviewStore(path, {"train-image"}, {"property-a"}, ["kitchen", "bathroom"])
    saved = store.save(payload())
    assert saved["revision"] == 1
    assert saved["images"]["train-image"]["features"]["old_flooring"] is None
    loaded = ReviewStore(path, {"train-image"}, {"property-a"}, ["kitchen"])
    assert loaded.snapshot() == saved
    with pytest.raises(RuntimeError, match="another window"):
        loaded.save(payload())
    assert len(loaded.snapshot()["history"]) == 1
    with pytest.raises(ValueError, match="training review batch"):
        loaded.save(payload(id="test-holdout", expected_revision=1))


def test_validation_and_property_labels_are_independent(tmp_path) -> None:
    store = ReviewStore(tmp_path / "reviews.json", {"train-image"}, {"property-a"}, ["kitchen"])
    for changes in ({"room": None}, {"reviewer": ""}, {"features": {"old_cabinetry": "yes"}},
                    {"features": {"profit": 1}}, {"unexpected": "field"}):
        with pytest.raises(ValueError):
            store.save(payload(**changes))
    saved = store.save({
        "kind": "property", "id": "property-a", "expected_revision": 0,
        "reviewer": "Test", "status": "approved", "target_fit": "not_target",
        "reason": "Too turnkey", "notes": "",
    })
    assert saved["images"] == {}
    assert saved["properties"]["property-a"]["target_fit"] == "not_target"


def test_room_preferences_preserve_completed_reviews_and_protect_holdouts(tmp_path) -> None:
    path = tmp_path / "reviews.json"
    original = {
        "revision": 4,
        "images": {"train-image": {
            "status": "approved", "room": "bathroom", "features": {"old_cabinetry": None},
            "reviewer": "User",
        }},
        "properties": {"property-a": {
            "status": "approved", "target_fit": "not_target", "reason": "Too updated",
        }},
        "history": [],
    }
    path.write_text(json.dumps(original))
    store = ReviewStore(path, {"train-image"}, {"property-a"}, ["kitchen", "bathroom"],
                        {"train-image", "additional-training-image"})
    review = {
        "kind": "room_preference", "id": "train-image", "expected_revision": 4,
        "reviewer": "User", "status": "approved", "room": "bathroom",
        "preference": "target", "reason": "Worthwhile visible room work", "notes": "",
    }
    state = store.save(review)
    assert state["images"] == original["images"]
    assert state["properties"] == original["properties"]
    preference = state["room_preferences"]["train-image"]
    assert preference["room_label_source"] == "existing_human_approved_room"
    assert preference["property_target_inferred"] is False
    assert preference["feature_labels_inferred"] is False
    with pytest.raises(ValueError, match="training-only"):
        store.save({**review, "id": "heldout-image", "expected_revision": 5})
    assert store.snapshot()["revision"] == 5
    state = store.save({**review, "id": "additional-training-image", "expected_revision": 5})
    assert state["room_preferences"]["additional-training-image"]["room_label_source"] == (
        "grouping_only_unverified"
    )
    reloaded = ReviewStore(path, {"train-image"}, {"property-a"}, ["kitchen", "bathroom"],
                           {"train-image", "additional-training-image"})
    assert reloaded.snapshot() == state


def test_loopback_api_requires_origin_token_and_allowlisted_records(tmp_path) -> None:
    class FakeApp:
        token = "test-only-token"
        rows = {}
        store = ReviewStore(tmp_path / "reviews.json", {"train-image"}, {"property-a"}, ["kitchen"])

    server = create_server(0, FakeApp())
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{server.server_address[1]}"
    try:
        with urlopen(base + "/api/health") as response:
            assert json.load(response)["status"] == "ready"
        with pytest.raises(HTTPError) as blocked:
            urlopen(Request(base + "/api/reviews", data=json.dumps(payload()).encode(),
                            headers={"Content-Type": "application/json"}, method="POST"))
        assert blocked.value.code == 403
        headers = {"Origin": base, "X-Review-Token": FakeApp.token,
                   "Content-Type": "application/json"}
        with urlopen(Request(base + "/api/reviews", data=json.dumps(payload()).encode(),
                            headers=headers, method="POST")) as response:
            assert json.load(response)["revision"] == 1
        with pytest.raises(HTTPError) as bad_host:
            urlopen(Request(base + "/api/health", headers={"Host": "attacker.example"}))
        assert bad_host.value.code == 403
        with pytest.raises(HTTPError) as file:
            urlopen(base + "/images/not-a-manifest-key")
        assert file.value.code == 404
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_room_correction_preserves_annotations_and_invalidates_wrong_bucket_vote(tmp_path):
    path = tmp_path / "reviews.json"
    store = ReviewStore(path, {"a"}, {"property"}, ["kitchen", "bathroom"],
                        {"a"}, {"a": "train", "v": "validation"})
    vote = {
        "kind": "room_preference", "id": "a", "expected_revision": 0, "reviewer": "User",
        "status": "approved", "room": "kitchen", "preference": "target", "reason": "Work I want",
    }
    store.save(vote)
    result = store.save({
        "kind": "room_correction", "id": "a", "expected_revision": 1, "reviewer": "User",
        "status": "approved", "room": "bathroom", "reason": "Wrong room bucket",
    })
    assert result["room_corrections"]["a"]["training_allowed"] is True
    assert result["room_preferences"]["a"]["room"] == "kitchen"
    assert result["room_preferences"]["a"]["status"] == "needs_room_review"
    assert result["history"][-1]["invalidated_preferences"][0]["previous"]["preference"] == "target"
    assert result["images"] == {} and result["properties"] == {}
    with pytest.raises(ValueError, match="Room changed"):
        store.save({**vote, "expected_revision": 2})
    fixed = store.save({**vote, "expected_revision": 2, "room": "bathroom"})
    assert fixed["room_preferences"]["a"]["room_label_source"] == "existing_human_approved_room"
    assert ReviewStore(path, {"a"}, {"property"}, ["kitchen", "bathroom"],
                       {"a"}, {"a": "train", "v": "validation"}).snapshot() == fixed


def test_validation_feedback_is_persistent_but_never_training_preference(tmp_path):
    store = ReviewStore(tmp_path / "reviews.json", {"a"}, set(), ["kitchen", "bathroom"],
                        {"a"}, {"a": "train", "v": "validation", "t": "test"})
    result = store.save({
        "kind": "room_correction", "id": "v", "expected_revision": 0,
        "reviewer": "User", "status": "approved", "room": "bathroom",
    })
    assert result["room_corrections"]["v"]["training_allowed"] is False
    feedback = {
        "kind": "evaluation_preference", "id": "v", "expected_revision": 1,
        "reviewer": "User", "status": "approved", "room": "bathroom",
        "preference": "not_target", "reason": "Too updated",
    }
    result = store.save(feedback)
    assert result["evaluation_preferences"]["v"]["training_allowed"] is False
    assert result["evaluation_preferences"]["v"]["prediction_visible_during_review"] is True
    assert not result.get("room_preferences")
    with pytest.raises(ValueError):
        store.save({**feedback, "kind": "room_preference", "expected_revision": 2})
    with pytest.raises(ValueError):
        store.save({**feedback, "id": "a", "expected_revision": 2})

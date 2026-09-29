import copy

from learning_data import prepare_learning_data
from train_reviewed import approved_preferences, approved_room_reviews


def test_promotes_whole_reviewed_groups_without_touching_source_or_other_test_groups():
    manifest = {"dataset_sha256": "source", "images": [
        {"image_id": "train", "group_id": "A", "listing_key": "A", "split": "train"},
        {"image_id": "v1", "group_id": "B", "listing_key": "B", "split": "validation"},
        {"image_id": "v2", "group_id": "B", "listing_key": "B", "split": "validation"},
        {"image_id": "t1", "group_id": "C", "listing_key": "C", "split": "test"},
    ]}
    reviews = {"revision": 12, "images": {}, "room_preferences": {}, "room_corrections": {
        "v1": {"status": "approved", "room": "bathroom"},
    }, "evaluation_preferences": {
        "v1": {"status": "approved", "room": "bathroom", "preference": "target",
               "reviewer": "User", "updated_at": "2026-09-22", "reason": "Visible work"},
    }}
    original = copy.deepcopy((manifest, reviews))
    prepared, labels, partition = prepare_learning_data(manifest, reviews)
    assert [row["split"] for row in prepared["images"]] == ["train", "train", "train", "test"]
    assert partition["promoted_groups"] == ["B"]
    assert len(partition["promoted_images"]) == 2
    assert (manifest, reviews) == original
    rooms, excluded = approved_preferences(prepared, labels)
    assert rooms["bathroom"][0]["id"] == "v1"
    assert not excluded
    assert approved_room_reviews(prepared, labels)["v1"]["room"] == "bathroom"


def test_latest_invalidated_preference_cannot_fall_back_to_old_positive():
    manifest = {"dataset_sha256": "source", "images": [
        {"image_id": "v", "group_id": "A", "listing_key": "A", "split": "validation"},
    ]}
    reviews = {"revision": 2, "images": {}, "room_preferences": {
        "v": {"updated_at": "2026-09-23", "status": "needs_room_review", "room": "kitchen"},
    }, "evaluation_preferences": {
        "v": {"updated_at": "2026-09-22", "status": "approved", "room": "kitchen",
              "preference": "target"},
    }}
    prepared, labels, _ = prepare_learning_data(manifest, reviews)
    rooms, excluded = approved_preferences(prepared, labels)
    assert not rooms and not excluded

import copy

import pytest

from export_human_reviews import build_snapshot


def fixture():
    manifest = {
        "dataset_sha256": "example",
        "images": [{
            "image_id": "img1", "listing_key": "listing1", "group_id": "group1",
            "image_path": "example.jpg", "sha256": "imagehash", "split": "train",
            "room_label": "bathroom", "feature_labels": {"old_cabinetry": 1},
            "source_model": "draft", "photo_stage": "unknown",
        }],
    }
    batch = [
        {"image_id": "img1", "listing_key": "listing1", "address": "First property"},
        {"image_id": "img2", "listing_key": "listing2", "address": "Second property"},
    ]
    reviews = {
        "revision": 3,
        "images": {"img1": {
            "status": "approved", "room": "bedroom",
            "features": {"old_cabinetry": None}, "reviewer": "Reviewer",
            "updated_at": "2026-09-22T00:00:00Z",
        }},
        "properties": {"listing1": {
            "status": "approved", "target_fit": "not_target", "reason": "Too turnkey",
            "reviewer": "Reviewer", "updated_at": "2026-09-22T00:00:00Z",
        }},
    }
    return manifest, reviews, batch


def test_preserves_unknown_and_separates_property_preference():
    manifest, reviews, batch = fixture()
    original = copy.deepcopy((manifest, reviews, batch))
    result = build_snapshot(manifest, reviews, batch)
    assert result["summary"]["approved_images"] == 1
    assert result["summary"]["room_corrections"] == 1
    assert result["approved_image_labels"][0]["human_review"]["features"]["old_cabinetry"] is None
    assert result["approved_image_labels"][0]["target_fit"] is None
    assert result["summary"]["pending_property_decisions"] == [
        {"listing_key": "listing2", "address": "Second property"},
    ]
    assert result["summary"]["target_classifier_ready"] is False
    assert (manifest, reviews, batch) == original


def test_rejects_training_on_holdout():
    manifest, reviews, batch = fixture()
    manifest["images"][0]["split"] = "test"
    with pytest.raises(ValueError, match="holdout"):
        build_snapshot(manifest, reviews, batch)


def test_drafts_do_not_become_approved_labels():
    manifest, reviews, batch = fixture()
    reviews["images"]["img1"]["status"] = "draft"
    result = build_snapshot(manifest, reviews, batch)
    assert result["summary"]["approved_images"] == 0
    assert result["summary"]["room_correction_round_ready"] is False

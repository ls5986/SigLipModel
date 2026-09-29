import io
import json
import zipfile
from unittest.mock import patch

import pytest
from PIL import Image

from pilot import write_json
from studio_data import StudioStore, valid_zip_path


def archive(tmp_path, *, dataset=None, name="batch.zip", bad_path=None):
    buffer = io.BytesIO()
    Image.new("RGB", (20, 16), "white").save(buffer, format="JPEG")
    blob = buffer.getvalue()
    dataset = dataset or {
        "dataset_name": "Fixture",
        "properties": [{"id": "p1", "address": "<script>test</script>",
                        "metadata": {"YearBuilt": 1980, "OwnerPhone": "do-not-import",
                                     "StateOrProvince": "CA", "ParcelNumber": "P", "UnitNumber": "1"}}],
        "images": [{"id": "m1", "property_id": "p1", "path": "images/p1.jpg",
                    "proposed_room": "kitchen", "proposed_features": {"old_cabinetry": None}}],
    }
    file = tmp_path / name
    with zipfile.ZipFile(file, "w") as z:
        z.writestr("dataset.json", json.dumps(dataset))
        z.writestr("images/p1.jpg", blob)
        if bad_path:
            z.writestr(bad_path, "bad")
    return file


def imported_store(tmp_path):
    store = StudioStore(tmp_path / "pilot", tmp_path / "source")
    preview = store.preview_import(archive(tmp_path))
    assert preview["can_commit"]
    store.commit_import(preview["id"])
    return store


def test_summary_uses_review_statuses_without_rebuilding_property_proposals(tmp_path):
    store = imported_store(tmp_path)
    write_json(store.root / "data" / "human_reviews.json", {
        "images": {"p1:m1": {"status": "approved"}},
        "properties": {"p1": {"status": "approved"}},
    })
    with patch.object(store, "properties", side_effect=AssertionError("Summary rebuilt the dataset")), \
            patch.object(store, "property", side_effect=AssertionError("Summary loaded photo proposals")):
        counts = store.summary()["counts"]
        assert counts == {"properties": 1, "images": 1, "human_image_reviews": 1,
                          "human_property_reviews": 1, "pending_properties": 0, "missing_images": 0}
        store.save_review({"kind": "image", "id": "p1:m1", "expected_revision": 0,
                           "reviewer": "Test reviewer", "status": "draft", "room": "kitchen",
                           "features": {}, "preference": None})
        counts = store.summary()["counts"]
        assert counts["human_image_reviews"] == 0
        assert counts["human_property_reviews"] == 1
        assert counts["pending_properties"] == 1


def test_import_preview_no_commit_then_idempotent_and_private_fields_removed(tmp_path):
    store = StudioStore(tmp_path / "pilot", tmp_path / "source")
    result = store.preview_import(archive(tmp_path))
    assert result["properties"] == result["images"] == 1
    assert store.properties()["total"] == 0
    assert store.commit_import(result["id"])["human_approvals_created"] == 0
    assert store.commit_import(result["id"])["already_committed"]
    prop = store.property("p1")
    assert "OwnerPhone" not in prop["property"]["metadata"]
    assert prop["property"]["review"].get("status") is None
    assert prop["images"][0]["features"]["old_cabinetry"] is None
    assert store.image_path("p1:m1").is_file()
    with pytest.raises(ValueError):
        store.image_path("../../.env")


@pytest.mark.parametrize("path", ["../escape", "/absolute", "C:/bad"])
def test_zip_traversal_is_rejected_and_not_extracted(tmp_path, path):
    store = StudioStore(tmp_path / "pilot", tmp_path / "source")
    result = store.preview_import(archive(tmp_path, bad_path=path))
    assert not result["can_commit"]
    assert result["errors"]
    with pytest.raises(ValueError):
        store.commit_import(result["id"])


def test_manifest_backslash_paths_are_rejected():
    with pytest.raises(ValueError):
        valid_zip_path("dir\\bad")


def test_review_is_explicit_conflict_checked_and_separate_from_property(tmp_path):
    store = imported_store(tmp_path)
    payload = {
        "kind": "image", "id": "p1:m1", "expected_revision": 0, "reviewer": "Reviewer",
        "status": "approved", "room": "bathroom",
        "features": {"old_cabinetry": False, "old_flooring": None}, "preference": "target",
    }
    saved = store.save_review(payload)
    assert saved["revision"] == 1
    assert saved["features"]["old_flooring"] is None
    assert saved["property_target_inferred"] is False
    assert store.property("p1")["property"]["review"].get("target_fit") is None
    with pytest.raises(RuntimeError):
        store.save_review(payload)
    store.save_review({"kind": "property", "id": "p1", "expected_revision": 0,
                       "reviewer": "Reviewer", "status": "approved", "target_fit": "not_target",
                       "condition_label": "mixed", "reason": "Not enough scope"})
    restored = StudioStore(store.root, store.source)
    assert restored.property("p1")["images"][0]["review"]["room"] == "bathroom"
    assert restored.property("p1")["images"][0]["review"]["preference"] == "target"


def test_llm_apply_creates_drafts_and_never_overwrites_human_labels(tmp_path):
    store = imported_store(tmp_path)
    store.save_review({"kind": "image", "id": "p1:m1", "expected_revision": 0,
                       "reviewer": "Reviewer", "status": "approved", "room": "bathroom",
                       "features": {"old_cabinetry": None}, "preference": "unsure"})
    bundle = [{"property_id": "p1", "run_id": "run", "prompt_hash": "hash", "model": "fake",
               "source": "llm", "images": [{"image_id": "p1:m1", "room": "kitchen",
                                           "features": {"old_cabinetry": True}}],
               "property": {"condition_label": "UPDATED_TURNKEY", "summary": "Draft"}}]
    store.apply_proposals(bundle)
    store.apply_proposals(bundle)
    result = store.property("p1")
    assert result["images"][0]["room"] == "bathroom"
    assert result["images"][0]["features"]["old_cabinetry"] is None
    assert len(result["property_suggestions"]) == 1
    assert result["property"]["review"].get("target_fit") is None


def test_import_duplicate_image_preserves_protected_split(tmp_path):
    store = imported_store(tmp_path)
    with store.connect() as db:
        db.execute("UPDATE images SET split='test',group_id='protected'")
    dataset = {
        "properties": [{"id": "p2", "address": "Other"}],
        "images": [{"id": "m2", "property_id": "p2", "path": "images/p1.jpg"}],
    }
    preview = store.preview_import(archive(tmp_path, dataset=dataset, name="second.zip"))
    assert preview["duplicates"] == 1
    store.commit_import(preview["id"])
    assert store.property("p2")["images"][0]["split"] == "test"
    with store.connect() as db:
        assert db.execute("SELECT group_id FROM images WHERE id='p2:m2'").fetchone()[0] == "protected"


def test_import_bridge_updates_existing_training_group_to_protected(tmp_path):
    store = imported_store(tmp_path)
    with store.connect() as db:
        db.execute("INSERT INTO properties VALUES ('p3','parcel:ca:p:1','{}','fixture','now')")
        db.execute("INSERT INTO images VALUES ('p3:m3','p3','not-served','different',1,"
                   "'test','reserved','fixture','now')")
        store._reconcile_groups(db)
    assert store.property("p1")["images"][0]["split"] == "test"


@pytest.mark.parametrize("dataset", [
    [], {"properties": ["not-an-object"], "images": [{}]},
    {"properties": [{"id": []}], "images": [{}]},
    {"properties": [{"id": "p1"}], "images": [{"id": "m1", "property_id": "p1",
                                              "path": "images/p1.jpg", "order": {}}]},
])
def test_bad_manifest_is_readable_preview_error(tmp_path, dataset):
    store = StudioStore(tmp_path / "pilot", tmp_path / "source")
    file = archive(tmp_path)
    with zipfile.ZipFile(tmp_path / "bad.zip", "w") as target, zipfile.ZipFile(file) as source:
        target.writestr("dataset.json", json.dumps(dataset))
        target.writestr("images/p1.jpg", source.read("images/p1.jpg"))
    result = store.preview_import(tmp_path / "bad.zip")
    assert not result["can_commit"]
    assert result["errors"]


def test_strict_room_score_filter_precedes_pagination_and_excludes_unknown(tmp_path):
    store = imported_store(tmp_path)
    with store.connect() as db:
        for number in (2, 3, 4):
            db.execute("INSERT INTO images SELECT ?,property_id,path,sha256,sequence,"
                       "split,group_id,source,created_at FROM images WHERE id='p1:m1'",
                       (f"p1:m{number}",))
    store.apply_proposals([{
        "property_id": "p1", "run_id": "local", "prompt_hash": "hash", "source": "local-siglip-draft",
        "images": [{"image_id": f"p1:m{i}", "room": "kitchen", "room_preference_score": score}
                   for i, score in enumerate((.2, .54, .55, None), 1)], "property": {},
    }])
    page = store.image_list(score_above=".54", limit=1)
    assert page["total"] == 1
    assert page["items"][0]["id"] == "p1:m3"
    with pytest.raises(ValueError):
        store.image_list(score_above="nan")

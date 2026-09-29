import json
import threading
from types import SimpleNamespace
from unittest.mock import patch

import joblib
import pytest

import pilot
from pilot import write_json
from studio_jobs import StudioJobs
from studio_worker import (
    group_data,
    load_starting_bundle,
    supervised_indices,
    verify_image_snapshot,
)
from test_studio_data import imported_store


def test_transitive_property_and_hash_bridge_protects_every_example():
    examples = [
        {"group_id": "a", "split": "train", "sha256": "h1", "physical_key": "parcel1"},
        {"group_id": "b", "split": "learning", "sha256": "h2", "physical_key": "parcel1"},
        {"group_id": "c", "split": "test", "sha256": "h2", "physical_key": "parcel2"},
    ]
    groups, protected = group_data(examples)
    assert len(set(groups)) == 1
    assert groups[0] in protected
    assert supervised_indices(examples, [True, True, True], groups, protected) == ([], 0)


def test_unknowns_duplicate_conflicts_and_repeated_angles_not_independent():
    examples = [{"sha256": key} for key in ("h1", "h1", "h2", "h2", "h3", "h4")]
    chosen, conflicts = supervised_indices(
        examples, [1, 1, 0, 1, None, 0], ["a", "a", "b", "b", "c", "test"], {"test"},
    )
    assert chosen == [0]
    assert conflicts == 1


def test_property_target_and_machine_proposals_do_not_train_image_heads(tmp_path):
    store = imported_store(tmp_path)
    store.save_review({"kind": "property", "id": "p1", "expected_revision": 0,
                       "reviewer": "Reviewer", "status": "approved", "target_fit": "target",
                       "condition_label": "dated", "reason": "Scope"})
    jobs = StudioJobs(store, store.root)
    examples, _ = jobs._snapshot()
    assert examples[0]["room"] is None
    assert examples[0]["features"] == {}
    assert examples[0]["preference"] is None
    store.save_review({"kind": "image", "id": "p1:m1", "expected_revision": 0,
                       "reviewer": "Reviewer", "status": "approved", "room": "bathroom",
                       "features": {"old_cabinetry": None}, "preference": "unsure"})
    examples, _ = jobs._snapshot()
    assert examples[0]["room"] == "bathroom"
    assert examples[0]["features"]["old_cabinetry"] is None
    write_json(store.root / "data" / "manifest.json", {"images": []})
    preview = jobs.preview("train")
    assert preview["approved_rooms"] == 1
    assert preview["approved_preferences"] == 0
    assert preview["new_embeddings_needed"] == 1


def test_explicit_prelabel_start_freezes_labels_and_has_no_cloud_transport(tmp_path):
    store = imported_store(tmp_path)
    jobs = StudioJobs(store, store.root)
    write_json(store.root / "data" / "manifest.json", {"images": []})
    preview = jobs.preview("prelabel")
    gate = threading.Event()
    assert jobs.active is None
    snapshot = json.loads((jobs.folder / preview["id"] / "snapshot.json").read_text())
    assert snapshot["examples"][0]["room"] is None
    with patch.object(jobs, "_run", side_effect=lambda folder: gate.wait(2)) as worker:
        status = jobs.start({"id": preview["id"]}, "prelabel")
        assert status["status"] == "running"
        gate.set()
        jobs.active.join(3)
        assert worker.call_count == 1


def test_explicit_new_unknown_does_not_resurrect_old_preference(tmp_path):
    store = imported_store(tmp_path)
    write_json(store.root / "data" / "human_reviews.json", {
        "room_preferences": {"p1:m1": {"room": "kitchen", "preference": "target", "status": "approved"}},
    })
    store.save_review({"kind": "image", "id": "p1:m1", "expected_revision": 0,
                       "reviewer": "Reviewer", "status": "approved", "room": "kitchen",
                       "features": {}, "preference": None})
    rows, _ = StudioJobs(store, store.root)._snapshot()
    assert rows[0]["preference"] is None


def test_success_publishes_version_and_future_jobs_reuse_it(tmp_path, monkeypatch):
    store = imported_store(tmp_path)
    jobs = StudioJobs(store, store.root)
    write_json(store.root / "data" / "manifest.json", {"images": []})
    preview = jobs.preview("train")
    folder = jobs.folder / preview["id"]
    joblib.dump({"marker": "new-version"}, folder / "studio_heads.joblib")
    write_json(folder / "metrics.json", {"human_fit": {}})
    write_json(folder / "proposals.json", [{
        "property_id": "p1", "run_id": folder.name, "prompt_hash": "local-test",
        "source": "local-siglip-draft", "images": [{
            "image_id": "p1:m1", "room": "bathroom", "features": {},
            "room_preference_score": .71,
        }], "property": {"condition_label": "UNKNOWN"},
    }])
    with patch("studio_jobs.subprocess.run", return_value=SimpleNamespace(returncode=0)):
        jobs._run(folder)
    assert jobs.list_jobs()[0]["status"] == "completed"
    assert not (store.root / "artifacts" / "candidate_latest.json").exists()
    monkeypatch.setattr(pilot, "ARTIFACTS", store.root / "artifacts")
    bundle, version = load_starting_bundle()
    assert bundle == {"marker": "new-version"}
    assert version == folder.name
    photo = store.property("p1")["images"][0]
    assert photo["room_source"] == "local-siglip-draft"
    assert photo["local_model"]["preferences"]["bathroom"]["score"] == .71
    assert photo["review"]["status"] == "unreviewed"
    (folder / "studio_heads.joblib").write_bytes(b"changed")
    with pytest.raises(ValueError, match="hash changed"):
        load_starting_bundle()


def test_cached_images_are_checked_against_original_bytes(tmp_path):
    path = tmp_path / "original.jpg"
    path.write_bytes(b"original")
    examples = [{"path": str(path), "sha256": pilot.sha(path)}]
    verify_image_snapshot(examples)
    path.write_bytes(b"changed")
    with pytest.raises(ValueError, match="cannot be reused"):
        verify_image_snapshot(examples)

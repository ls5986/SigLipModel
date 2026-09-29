import threading
from pathlib import Path
from unittest.mock import patch

import pytest

from pilot import write_json
from training_jobs import TrainingManager


class Store:
    def snapshot(self):
        return {"revision": 9, "images": {}, "room_preferences": {},
                "properties": {}, "history": []}


def test_train_endpoint_snapshots_reviews_and_runs_only_once(tmp_path):
    gate = threading.Event()
    manager = TrainingManager(Store(), tmp_path)

    def fake_run(folder: Path):
        assert (folder / "reviews.json").exists()
        gate.wait(3)
        with manager.lock:
            manager.state["status"] = "completed"

    with patch.object(manager, "_run", side_effect=fake_run) as run:
        result = manager.start({"expected_revision": 9})
        same = manager.start({"expected_revision": 9})
        assert result["job_id"] == same["job_id"]
        gate.set()
        manager.thread.join(timeout=4)
        assert run.call_count == 1
    with pytest.raises(RuntimeError, match="another window"):
        manager.start({"expected_revision": 8})


def test_no_unnecessary_retrain_when_same_learning_revision_is_current(tmp_path):
    write_json(tmp_path / "artifacts" / "candidate_latest.json", {
        "learning_mode": True, "review_revision": 9, "version": "existing",
    })
    manager = TrainingManager(Store(), tmp_path)
    result = manager.start({"expected_revision": 9})
    assert result["status"] == "completed"
    assert result["version"] == "existing"
    assert manager.thread is None

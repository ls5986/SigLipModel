"""Single explicit local training job, frozen review snapshot, and durable status."""

import json
import os
import subprocess
import sys
import threading
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

from config import CODE_ROOT
from pilot import ROOT, read_json, write_json


class TrainingManager:
    def __init__(self, store, root: Path = ROOT):
        self.store = store
        self.root = root
        self.lock = threading.RLock()
        self.state = {"status": "idle"}
        self.thread = None
        path = root / "artifacts" / "training_job.json"
        if path.exists():
            self.state = read_json(path)
            if self.state.get("status") == "running":
                self.state = {**self.state, "status": "interrupted",
                              "message": "Server restarted. Press Train to retry; reviews are preserved."}

    def snapshot(self):
        with self.lock:
            return json.loads(json.dumps(self.state))

    def start(self, payload):
        if set(payload) != {"expected_revision"}:
            raise ValueError("Train request requires only expected_revision")
        with self.lock:
            if self.thread and self.thread.is_alive():
                return self.snapshot()
            reviews = self.store.snapshot()
            if payload["expected_revision"] != reviews["revision"]:
                raise RuntimeError("Reviews changed in another window. Reload answers before training.")
            pointer_path = self.root / "artifacts" / "candidate_latest.json"
            if pointer_path.exists():
                pointer = read_json(pointer_path)
                if pointer.get("learning_mode") and pointer["review_revision"] == reviews["revision"]:
                    self.state = {"status": "completed", "version": pointer["version"],
                                  "review_revision": reviews["revision"],
                                  "message": "This saved revision is already trained; current predictions loaded."}
                    return self.snapshot()
            job_id = uuid4().hex
            folder = self.root / "artifacts" / "training_jobs" / job_id
            folder.mkdir(parents=True)
            write_json(folder / "reviews.json", reviews)
            self.state = {
                "status": "running", "job_id": job_id, "review_revision": reviews["revision"],
                "started_at": datetime.now(UTC).isoformat(),
                "message": "Training small classifiers from cached embeddings, then re-predicting.",
                "log": str(folder / "train.log"),
            }
            write_json(self.root / "artifacts" / "training_job.json", self.state)
            self.thread = threading.Thread(target=self._run, args=(folder,), daemon=True)
            self.thread.start()
            return self.snapshot()

    def _run(self, folder):
        try:
            with (folder / "train.log").open("w", encoding="utf-8") as log:
                process = subprocess.run(
                    [sys.executable, "-B", str(CODE_ROOT / "train_reviewed.py"), "--learning",
                     "--reviews-snapshot", str(folder / "reviews.json")],
                    cwd=self.root, stdout=log, stderr=subprocess.STDOUT,
                    env={**os.environ, "ACQ_DATA_ROOT": str(self.root), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                    timeout=1200, check=False,
                )
            if process.returncode:
                raise RuntimeError(f"Training exited with code {process.returncode}. See the local log.")
            pointer = read_json(self.root / "artifacts" / "candidate_latest.json")
            with self.lock:
                if pointer["review_revision"] != self.state["review_revision"]:
                    raise RuntimeError("Published candidate does not match this job's saved review revision")
                self.state.update(status="completed", version=pointer["version"],
                                  finished_at=datetime.now(UTC).isoformat(),
                                  message="Model trained and predictions regenerated. Reviews preserved.")
        except (OSError, RuntimeError, subprocess.TimeoutExpired) as exc:
            with self.lock:
                self.state.update(status="failed", finished_at=datetime.now(UTC).isoformat(),
                                  message=str(exc))
        with self.lock:
            write_json(self.root / "artifacts" / "training_job.json", self.state)

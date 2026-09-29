"""Local embedding/prelabel/training jobs. Explicit previews, isolated artifacts, no cloud."""

import os
import subprocess
import sys
import threading
from uuid import uuid4

from config import CODE_ROOT
from pilot import ROOT, read_json, sha, write_json
from studio_data import now


class StudioJobs:
    def __init__(self, store, root=ROOT):
        self.store, self.root = store, root
        self.folder = root / "artifacts" / "studio_jobs"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.active = None
        for file in self.folder.glob("*/status.json"):
            data = read_json(file)
            if data["status"] == "running":
                data.update(status="interrupted", error="Local server restarted. Create a new preview to retry.")
                write_json(file, data)

    def list_jobs(self):
        with self.lock:
            result = []
            for file in self.folder.glob("*/status.json"):
                data = read_json(file)
                result.append(data)
            return sorted(result, key=lambda row: row["created_at"], reverse=True)[:15]

    def _snapshot(self):
        with self.store.connect() as db:
            props = {row["id"]: row["physical_key"] for row in
                     db.execute("SELECT id,physical_key FROM properties ORDER BY id")}
            rows = {row["id"]: dict(row) for row in db.execute("SELECT * FROM images")}
        legacy = self.store.legacy()
        examples = []
        for key in props:
            prop = self.store.property(key, legacy)
            for item in prop["images"]:
                row = rows[item["id"]]
                review = item["review"]
                room_approved = review.get("status") == "approved" or review.get("room_confirmed")
                preference = review.get("preference") if review.get("status") == "approved" else None
                # Legacy room preferences were reviewed independently of room/feature labels.
                if not preference and review.get("legacy"):
                    choices = [legacy.get(field, {}).get(item["id"]) for field in
                               ("room_preferences", "evaluation_preferences")]
                    choices = [value for value in choices if value and value.get("status") == "approved"]
                    latest = max(choices, key=lambda r: r.get("updated_at", "")) if choices else None
                    if latest and latest.get("room") == item["room"]:
                        preference = latest.get("preference")
                examples.append({
                    "id": item["id"], "property_id": key, "group_id": row["group_id"],
                    "split": row["split"], "sha256": row["sha256"], "path": row["path"],
                    "physical_key": props[key],
                    "room": review.get("room") if room_approved else None,
                    "proposed_room": item["room"],
                    "features": review.get("features", {}) if review.get("status") == "approved" else {},
                    "preference": preference,
                    "preference_room": review.get("preference_room") or item["room"],
                    "human_review_revision": review.get("revision", 0),
                    "photo_context": review.get("context") or item.get("provider_context"),
                })
        return examples, legacy.get("revision", 0)

    def preview(self, kind="train"):
        if kind not in {"train", "prelabel"}:
            raise ValueError("Unsupported local job")
        examples, revision = self._snapshot()
        if not examples:
            raise ValueError("Import photos before creating a local job")
        token = uuid4().hex
        folder = self.folder / token
        folder.mkdir()
        old_manifest = read_json(self.root / "data" / "manifest.json")
        cache_path = self.root / "data" / "studio_vectors.npz"
        import numpy as np
        known = {row["sha256"] for row in old_manifest["images"]}
        if cache_path.exists():
            with np.load(cache_path, allow_pickle=False) as cache:
                known.update(cache["sha256"].tolist())
        new = {row["sha256"] for row in examples if row["sha256"] not in known}
        if len(new) > 15000:
            raise ValueError("Too many new images; use an import batch of at most 15,000")
        visible = [row for row in examples if row["split"] != "test"]
        approved_rooms = sum(row["room"] is not None for row in visible)
        approved_preferences = sum(row["preference"] in {"target", "not_target"} for row in visible)
        preview = {
            "id": token, "kind": kind, "approved_rooms": approved_rooms,
            "approved_preferences": approved_preferences, "eligible_images": len(visible),
            "new_embeddings_needed": len(new), "groups": len({row["group_id"] for row in visible}),
            "protected_groups": len({row["group_id"] for row in examples if row["split"] == "test"}),
            "approved_features": sum(value is not None for row in visible
                                     for value in row["features"].values()),
            "warnings": [
                "Frozen SigLIP embeddings run locally; no image uploads or paid calls.",
                "Only explicitly approved image labels and decisive photo preferences supervise new heads.",
                "Property Target/Not target is not copied onto every photo. Unknown labels are masked.",
                "Existing benchmark groups stay protected. Internal grouped validation is not blinded testing.",
                "New image embedding on CPU may take time. Progress and errors will be shown.",
            ],
        }
        from model_loop import fingerprint
        snapshot = {"review_fingerprint": fingerprint(examples), "kind": kind, "examples": examples, "legacy_review_revision": revision,
                    "preview": preview, "created_at": now()}
        write_json(folder / "snapshot.json", snapshot)
        write_json(folder / "status.json", {"id": token, "kind": kind, "status": "preview",
                                           "detail": preview, "created_at": snapshot["created_at"]})
        return preview

    def start(self, payload, kind="train"):
        identifier = payload.get("id")
        if not isinstance(identifier, str) or len(identifier) != 32 or not all(
            char in "0123456789abcdef" for char in identifier
        ):
            raise ValueError("Unknown job preview")
        folder = self.folder / identifier
        with self.lock:
            if self.active and self.active.is_alive():
                raise RuntimeError("A local job is already running. Wait for its result.")
            if not (folder / "snapshot.json").exists():
                raise ValueError("Create a preview before starting")
            snapshot = read_json(folder / "snapshot.json")
            if snapshot["kind"] != kind:
                raise ValueError("Preview type mismatch")
            status = read_json(folder / "status.json")
            if status["status"] in {"completed", "running"}:
                return status
            status.update(status="running", started_at=now(), error=None,
                          detail="Preparing local image vectors; original model stays available")
            write_json(folder / "status.json", status)
            self.active = threading.Thread(target=self._run, args=(folder,), daemon=True)
            self.active.start()
            return status

    def _run(self, folder):
        try:
            with (folder / "log.txt").open("w", encoding="utf-8") as log:
                result = subprocess.run(
                    [sys.executable, "-B", str(CODE_ROOT / "studio_worker.py"),
                     "--job", str(folder)],
                    cwd=self.root, stdout=log, stderr=subprocess.STDOUT, timeout=7200,
                    env={**os.environ, "ACQ_DATA_ROOT": str(self.root), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"},
                    check=False,
                )
            if result.returncode:
                lines = (folder / "log.txt").read_text(encoding="utf-8", errors="replace").splitlines()
                detail = next((line for line in reversed(lines) if line.strip()), "No worker output")
                raise RuntimeError(f"Local worker failed (exit {result.returncode}): {detail[:400]}")
            output = read_json(folder / "proposals.json")
            self.store.apply_proposals(output)
            if (folder / "studio_heads.joblib").exists():
                metrics = read_json(folder / "metrics.json")
                write_json(self.root / "artifacts" / "studio_candidate_latest.json", {
                    "version": folder.name, "folder": str(folder),
                    "heads_sha256": sha(folder / "studio_heads.joblib"),
                    "snapshot_sha256": sha(folder / "snapshot.json"),
                    "metrics": metrics, "created_at": now(),
                    "review_fingerprint": read_json(folder / "snapshot.json").get("review_fingerprint"),
                })
            with self.lock:
                status = read_json(folder / "status.json")
                status.update(status="completed", finished_at=now(),
                              detail="Local predictions saved as editable proposals. No human labels overwritten.")
                write_json(folder / "status.json", status)
        except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired) as exc:
            with self.lock:
                status = read_json(folder / "status.json")
                status.update(status="failed", finished_at=now(), error=str(exc),
                              detail="Prior models and human reviews preserved; no success claimed.")
                write_json(folder / "status.json", status)


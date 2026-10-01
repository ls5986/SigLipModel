"""Bounded, explicit file exchange with ACQ BOT. No credentials or cloud writes."""
import hashlib
import json
import os
import subprocess
import sys
import threading
from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from config import CODE_ROOT
from model_loop import candidate, status
from pilot import read_json, write_json
from property_models import ACCEPTED_METADATA_KEYS, PREDICTION_MODES


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()


def validate_request(request):
    if not isinstance(request, dict):
        raise ValueError("Choose an ACQ BOT test request JSON file")
    if len(json.dumps(request, ensure_ascii=True)) > 100_000:
        raise ValueError("Comparison request is too large")
    version = request.get("schema_version")
    fields = {
        "acq-siglip-request-v1": {
            "schema_version", "comparison_id", "baseline", "photos", "request_sha256",
        },
        "acq-property-request-v2": {
            "schema_version", "comparison_id", "property_id", "baseline",
            "metadata", "photos", "requested_mode", "request_sha256",
        },
    }
    if version not in fields or set(request) != fields[version]:
        raise ValueError("Unsupported test request version")
    UUID(request["comparison_id"])
    if digest({k: v for k, v in request.items() if k != "request_sha256"}) != request["request_sha256"]:
        raise ValueError("Test request checksum mismatch")
    photos = request["photos"]
    minimum = 1 if version == "acq-siglip-request-v1" else 0
    if not isinstance(photos, list) or not minimum <= len(photos) <= 12:
        raise ValueError(f"Choose a request with {minimum} to 12 photos")
    if version == "acq-property-request-v2":
        if not isinstance(request["property_id"], str) or not 1 <= len(request["property_id"]) <= 200:
            raise ValueError("Invalid property identity")
        if request["requested_mode"] not in PREDICTION_MODES:
            raise ValueError("Unsupported prediction mode")
        if not isinstance(request["baseline"], dict):
            raise ValueError("Invalid baseline summary")
        metadata = request["metadata"]
        if not isinstance(metadata, dict) or set(metadata) - ACCEPTED_METADATA_KEYS:
            raise ValueError("Metadata contains unsupported or potentially post-decision fields")
        if len(json.dumps(metadata, ensure_ascii=True)) > 32_000:
            raise ValueError("Metadata snapshot is too large")
        if any(isinstance(value, (dict, list)) for value in metadata.values()):
            raise ValueError("Metadata values must be scalar")
        if not photos and not metadata:
            raise ValueError("Request needs photos, supported metadata, or both")
    hosts = set()
    for i, photo in enumerate(photos, 1):
        if set(photo) != {"image_id", "url"} or photo["image_id"] != f"photo-{i}":
            raise ValueError("Unexpected photo identity")
        if not isinstance(photo["url"], str) or len(photo["url"]) > 4096:
            raise ValueError("Invalid photo URL")
        url = urlsplit(photo["url"])
        if url.scheme != "https" or not url.hostname or url.username or url.password or url.port not in {None, 443}:
            raise ValueError("Only public HTTPS image URLs on port 443 are supported")
        hosts.add(url.hostname)
    return sorted(hosts)


class Exchange:
    def __init__(self, jobs):
        self.jobs = jobs
        self.folder = jobs.root / "artifacts" / "acq_comparisons"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = jobs.lock
        self.active = None
        self.processes = {}
        for path in self.folder.glob("*/status.json"):
            state = read_json(path)
            if state["status"] == "running":
                write_json(path, {**state, "status": "interrupted", "error": "Studio restarted. Create a new preview."})

    def _folder(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Unknown comparison")
        return self.folder / identifier

    def preview(self, request):
        hosts = validate_request(request)
        state = status(self.jobs)
        if not state["ready"]:
            raise ValueError(state["reason"])
        pointer, file = candidate(self.jobs.root)
        identifier = uuid4().hex
        folder = self._folder(identifier)
        folder.mkdir()
        frozen = {"request": request, "model_file": str(file), "model": state,
                  "training_snapshot": str(file.parent / "snapshot.json")}
        write_json(folder / "input.json", frozen)
        result = {"id": identifier, "status": "preview", "hosts": hosts,
                  "photos": len(request["photos"]), "model_version": pointer["version"],
                  "baseline": request["baseline"],
                  "requested_mode": request.get("requested_mode", "images_only")}
        write_json(folder / "status.json", result)
        return result

    def start(self, payload):
        if payload.get("confirmed") is not True:
            raise ValueError("Confirm the photo download and local processing first")
        folder = self._folder(payload.get("id"))
        with self.lock:
            state = read_json(folder / "status.json")
            if state["status"] in {"running", "completed"}:
                return state
            if state["status"] != "preview":
                raise ValueError("Create a new preview to retry")
            if self.active and self.active.is_alive() or self.jobs.active and self.jobs.active.is_alive():
                raise ValueError("A local model job is already running")
            frozen = read_json(folder / "input.json")
            current = status(self.jobs)
            if not current["ready"] or current["heads_sha256"] != frozen["model"]["heads_sha256"] or current["review_fingerprint"] != frozen["model"]["review_fingerprint"]:
                raise ValueError("Model or reviews changed. Train if needed and create a new preview.")
            state["status"] = "running"
            write_json(folder / "status.json", state)
            self.active = threading.Thread(target=self._run, args=(folder,), daemon=True)
            self.jobs.active = self.active
            self.active.start()
            return state

    def _run(self, folder):
        try:
            with (folder / "log.txt").open("w", encoding="utf-8") as log:
                with self.lock:
                    if (folder / "cancelled").exists():
                        return
                    process = subprocess.Popen([sys.executable, "-B", str(CODE_ROOT / "comparison_worker.py"), "--job", str(folder)],
                                        stdout=log, stderr=subprocess.STDOUT,
                                        env={**os.environ, "ACQ_DATA_ROOT": str(self.jobs.root), "HF_HUB_OFFLINE": "1", "TRANSFORMERS_OFFLINE": "1"})
                    self.processes[folder.name] = process
                try:
                    returncode = process.wait(timeout=600)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait()
                    raise
            if (folder / "cancelled").exists():
                return
            if returncode:
                raise ValueError("Local comparison failed. See this comparison's log.txt; no result was imported into ACQ BOT.")
            report = read_json(folder / "result.json")
            current = status(self.jobs)
            if current["review_fingerprint"] != report["model"]["review_fingerprint"]:
                raise ValueError("Reviews changed during processing. Retrain and create a new comparison.")
            state = read_json(folder / "status.json")
            write_json(folder / "status.json", {**state, "status": "completed"})
        except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
            state = read_json(folder / "status.json")
            write_json(folder / "status.json", {**state, "status": "failed", "error": str(exc)})

        finally:
            with self.lock:
                self.processes.pop(folder.name, None)
            for image in folder.glob("*.image"):
                image.unlink(missing_ok=True)

    def cancel(self, identifier):
        folder = self._folder(identifier)
        with self.lock:
            (folder / "cancelled").touch()
            process = self.processes.get(identifier)
            if process and process.poll() is None:
                process.terminate()
            state = read_json(folder / "status.json")
            write_json(folder / "status.json", {**state, "status": "cancelled"})

    def result(self, identifier):
        folder = self._folder(identifier)
        state = read_json(folder / "status.json")
        if state["status"] == "completed":
            state["result"] = read_json(folder / "result.json")
        return state

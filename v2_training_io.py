"""Bounded training I/O. Never changes a frozen example, label, or split."""
from __future__ import annotations

from contextlib import nullcontext
from contextvars import ContextVar
from datetime import datetime, timezone
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import traceback
from uuid import UUID

import httpx
import numpy as np

_ACTIVE = ContextVar("actvision_training_progress", default=None)


def _now():
    return datetime.now(timezone.utc).isoformat()


def safe_error(error):
    """Keep code locations and exception types, never messages, URLs or locals."""
    chain = []
    seen = set()
    current = error
    while current is not None and id(current) not in seen and len(chain) < 6:
        seen.add(id(current))
        chain.append(type(current).__name__)
        current = current.__cause__ or current.__context__
    frames = [{"file": Path(frame.filename).name, "function": frame.name, "line": frame.lineno}
              for frame in traceback.extract_tb(error.__traceback__)[-8:]]
    return {"error_type": type(error).__name__, "exception_chain": chain, "frames": frames}


class Progress:
    def __init__(self, store, training_id):
        self.store = store
        self.training_id = str(UUID(training_id))
        self.key = "actvision-v2-training-progress:" + self.training_id
        self.stage = None
        self.last_saved = 0.0
        self.latest = {}

    def emit(self, stage_name, *, force=False, **counts):
        self.latest = {**self.latest, **counts}
        changed = self.stage != stage_name
        self.stage = stage_name
        clock = time.monotonic()
        if not (force or changed or clock - self.last_saved >= 20):
            return
        payload = {"training_id": self.training_id, "stage": stage_name,
                   "at": _now(), **self.latest}
        # Logging/persisting progress must not corrupt an otherwise valid run.
        try:
            previous = self.store.document(self.key) or {}
            self.store.save_document(self.key, payload, previous.get("revision", 0))
            self.last_saved = clock
        except Exception as exc:
            print(json.dumps({"event": "training_progress_write_failed", "error_type": type(exc).__name__}), flush=True)
        print(json.dumps({"event": "actvision_training_progress", **payload}), flush=True)


def stage(name, **counts):
    progress = _ACTIVE.get()
    if progress is not None:
        progress.emit(name, **counts)


def observe_training(function):
    @wraps(function)
    def observed(store, request, siglip):
        progress = Progress(store, request["id"])
        token = _ACTIVE.set(progress)
        try:
            progress.emit("loading_frozen_dataset")
            result = function(store, request, siglip)
            progress.emit("candidate_saved", force=True, release_id=result["release_id"])
            return result
        except Exception as exc:
            failed_stage = progress.stage
            progress.emit("failed", force=True, failed_stage=failed_stage, diagnostic=safe_error(exc))
            raise
        finally:
            _ACTIVE.reset(token)
    return observed


def _transient_read(error):
    current = error
    seen = set()
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        if isinstance(current, (httpx.TimeoutException, httpx.NetworkError, httpx.RemoteProtocolError)):
            return True
        current = current.__cause__ or current.__context__
    return False


def _copy_verified(storage, photo, destination):
    for attempt in range(3):
        try:
            # get() uses this RLock too. Copy before any other cache user can prune
            # the returned path; batch-owned files remain alive through embed().
            with getattr(storage, "lock", nullcontext()):
                source = storage.get(photo["storage_bucket"], photo["storage_object_key"], photo["sha256"])
                shutil.copyfile(source, destination)
            if hashlib.sha256(destination.read_bytes()).hexdigest() != photo["sha256"]:
                raise OSError("Training batch photo integrity check failed")
            return
        except (OSError, httpx.TransportError) as exc:
            if attempt == 2 or not _transient_read(exc):
                raise
            print(json.dumps({"event": "training_photo_read_retry", "attempt": attempt + 1,
                              "exception_chain": safe_error(exc)["exception_chain"]}), flush=True)
            time.sleep(.5 * (2 ** attempt))


def artifact_preflight(store, request):
    """One small immutable upload/read probe, using the real worker credentials."""
    stage("artifact_storage_preflight")
    identifier = str(UUID(request["id"]))
    data = b'{"kind":"actvision-v2-storage-preflight","version":1}'
    sha = hashlib.sha256(data).hexdigest()
    key = f"models/preflight/{identifier}.json"
    try:
        store.storage.put("acq-training-private", key, data, content_type="application/json")
    except FileExistsError:
        # Reusing the same probe is safe only when the exact expected bytes exist.
        pass
    with tempfile.TemporaryDirectory(prefix="actvision-preflight-") as temporary:
        _copy_verified(store.storage, {"storage_bucket": "acq-training-private",
                       "storage_object_key": key, "sha256": sha}, Path(temporary) / "probe")
    stage("artifact_storage_verified")


def embed_rows(rows, siglip):
    """Embed every frozen photo exactly once, without staging the entire dataset."""
    storage = siglip._actvision_store.storage
    references = {}
    for row in rows:
        for photo in row.get("photos") or []:
            sha = photo["sha256"]
            if not re.fullmatch(r"[a-f0-9]{64}", sha):
                raise ValueError("Missing verified frozen photo identity")
            references.setdefault(sha, photo)
    identities = sorted(references)
    batch_size = int(os.environ.get("STUDIO_V2_EMBED_BATCH_SIZE", "8"))
    if not 1 <= batch_size <= 16:
        raise ValueError("STUDIO_V2_EMBED_BATCH_SIZE must be 1 through 16")
    vectors = {}
    dimensions = None
    stage("embedding_photos", embedded=0, total_photos=len(identities))
    for start in range(0, len(identities), batch_size):
        batch = identities[start:start + batch_size]
        with tempfile.TemporaryDirectory(prefix="actvision-photo-batch-") as temporary:
            paths = []
            for sha in batch:
                path = Path(temporary) / sha
                _copy_verified(storage, references[sha], path)
                paths.append(path)
            values = np.asarray(siglip.embed(paths), dtype=np.float32)
            if values.ndim != 2 or values.shape[0] != len(batch) or values.shape[1] == 0 or not np.isfinite(values).all():
                raise ValueError("Encoder returned invalid or incomplete photo embeddings")
            if dimensions is not None and values.shape[1] != dimensions:
                raise ValueError("Encoder embedding dimensions changed within the frozen dataset")
            dimensions = values.shape[1]
            for sha, vector in zip(batch, values, strict=True):
                vectors[sha] = vector.copy()
        stage("embedding_photos", embedded=len(vectors), total_photos=len(identities))
    stage("photo_embeddings_ready", embedded=len(vectors), total_photos=len(identities))
    return vectors

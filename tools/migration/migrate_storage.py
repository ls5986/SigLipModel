"""Content-addressed Supabase backup with bounded chunks and full download verification."""

import argparse
import base64
import hashlib
import json
import mimetypes
import os
import sqlite3
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

import httpx
from dotenv import dotenv_values
from migration_config import (
    HISTORICAL_ROOT,
    PILOT,
    PROJECT,
    REPORT_ROOT,
    require_project,
)

HERE = HISTORICAL_ROOT
STAGE = HERE / "migration_staging"
ROOTS = {
    "pilot": PILOT,
    "trestle-reports": REPORT_ROOT,
}
BASE = f"https://{PROJECT}.supabase.co/storage/v1"
BUCKET = "acq-training-private"
CHUNK = 6 * 1024 * 1024
SKIP_DIRS = {".venv", "__pycache__", ".pytest_cache", ".git", "node_modules", "migration_staging"}


def digest_file(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(CHUNK), b""):
            digest.update(chunk)
    return digest.hexdigest()


def save(path, payload):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    temporary.replace(path)


def file_parts(path, digest, is_photo):
    size = path.stat().st_size
    if is_photo and size <= CHUNK:
        return [{"key": "photos/" + digest + path.suffix.lower(), "offset": 0, "length": size,
                 "sha256": digest, "content_type": mimetypes.guess_type(path.name)[0] or "application/octet-stream"}]
    parts = []
    with path.open("rb") as handle:
        position = 0
        while chunk := handle.read(CHUNK):
            part_digest = hashlib.sha256(chunk).hexdigest()
            parts.append({"key": "archive/chunks/" + part_digest, "offset": position,
                          "length": len(chunk), "sha256": part_digest,
                          "content_type": "application/octet-stream"})
            position += len(chunk)
    return parts


def prepare():
    require_project()
    STAGE.mkdir(parents=True, exist_ok=True)
    if (STAGE / "upload_journal.json").exists():
        raise ValueError("Existing upload journal: resume transfer instead of replacing the manifest")
    snapshots = STAGE / "sqlite_snapshots"
    snapshots.mkdir(exist_ok=True)
    records = []
    for name, root in ROOTS.items():
        for directory, folders, files in os.walk(root):
            folders[:] = [folder for folder in folders if folder not in SKIP_DIRS
                          and not folder.startswith(".prompt-lab-tests")]
            for filename in sorted(files):
                original = Path(directory) / filename
                relative = original.relative_to(root)
                if filename.startswith(".env") or filename.endswith(("-wal", "-shm", ".tmp", ".lock")):
                    continue
                # Public pretrained weights can be reconstructed from the pinned revision;
                # keep their actual cached payload too, but not disposable download locks.
                if ".cache" in relative.parts and "huggingface" not in relative.parts:
                    continue
                if ".locks" in relative.parts or original.is_symlink():
                    continue
                path = original
                if original.suffix == ".sqlite3":
                    target = snapshots / (hashlib.sha256(str(original).encode()).hexdigest() + ".sqlite3")
                    with sqlite3.connect("file:" + original.as_posix() + "?mode=ro", uri=True) as source:
                        with sqlite3.connect(target) as destination:
                            source.backup(destination)
                    path = target
                digest = digest_file(path)
                photo = original.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"} and any(
                    folder in relative.parts for folder in ("originals", "media", "assets")
                )
                records.append({"root": name, "relative_path": str(relative), "source_path": str(path),
                                "original_path": str(original), "consistent_sqlite_snapshot": path != original,
                                "bytes": path.stat().st_size, "sha256": digest,
                                "parts": file_parts(path, digest, photo)})
    manifest = {
        "version": 1, "destination_project": PROJECT,
        "bucket": BUCKET, "created_at": datetime.now(UTC).isoformat(),
        "excluded": ["credentials/.env files", "virtual environments", "temporary files", "bytecode/test caches"],
        "records": records, "cutover_verified": False, "deletion_authorized": False,
    }
    save(STAGE / "file_manifest.json", manifest)
    parts = {part["key"]: part for row in records for part in row["parts"]}
    print(json.dumps({"manifest": str(STAGE / "file_manifest.json"), "files": len(records),
                      "unique_storage_objects": len(parts), "unique_upload_bytes": sum(p["length"] for p in parts.values()),
                      "uploaded": False, "deleted": False}), flush=True)


def client():
    values = dotenv_values(PILOT / ".env.migration")
    key = os.environ.get("SUPABASE_MIGRATION_STORAGE_KEY") or values.get("SUPABASE_MIGRATION_STORAGE_KEY")
    if not key:
        raise ValueError("Destination Storage key is missing; no upload attempted")
    if key.startswith("sb_publishable_"):
        raise ValueError("Saved key is publishable. Use the destination sb_secret_ key; no upload attempted")
    if not key.startswith("sb_secret_"):
        try:
            encoded = key.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
        except (IndexError, ValueError, UnicodeError):
            raise ValueError("Expected a destination secret key or legacy service_role key") from None
        if claims.get("role") != "service_role" or claims.get("ref") != PROJECT:
            raise ValueError("Legacy key must be service_role for the approved destination project")
    require_project()
    headers = {"apikey": key}
    if not key.startswith("sb_secret_"):
        headers["Authorization"] = "Bearer " + key
    return httpx.Client(headers=headers, timeout=90, follow_redirects=False)


def require(response, statuses):
    if response.status_code not in statuses:
        raise RuntimeError(f"Supabase Storage request failed with HTTP {response.status_code}; no success claimed")


def verify_object(http, part):
    digest = hashlib.sha256()
    size = 0
    with http.stream("GET", BASE + "/object/authenticated/" + BUCKET + "/" + quote(part["key"], safe="/")) as response:
        require(response, {200})
        for chunk in response.iter_bytes():
            size += len(chunk)
            if size > part["length"]:
                raise ValueError("Stored object exceeds expected length")
            digest.update(chunk)
    if size != part["length"] or digest.hexdigest() != part["sha256"]:
        raise ValueError("Stored object checksum mismatch")


def upload_part(http, path, part, already_uploaded=False):
    for attempt in range(3):
        try:
            if not already_uploaded:
                with path.open("rb") as handle:
                    handle.seek(part["offset"])
                    blob = handle.read(part["length"])
                if hashlib.sha256(blob).hexdigest() != part["sha256"]:
                    raise ValueError("Source chunk changed")
                response = http.post(
                    BASE + "/object/" + BUCKET + "/" + quote(part["key"], safe="/"),
                    headers={"Content-Type": part["content_type"], "x-upsert": "false"}, content=blob,
                )
                if response.status_code in {429, 500, 502, 503, 504} and attempt < 2:
                    time.sleep(2 ** attempt)
                    continue
                if response.status_code not in {200, 201, 409}:
                    if response.status_code != 400 or response.json().get("error") != "Duplicate":
                        require(response, {200, 201})
                already_uploaded = True
            verify_object(http, part)
            return part["key"], part["sha256"]
        except (httpx.TimeoutException, httpx.NetworkError):
            if attempt == 2:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("Object upload attempts exhausted")


def transfer():
    manifest_path = STAGE / "file_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("destination_project") != require_project():
        raise ValueError("Manifest destination differs from the approved project")
    journal_path = STAGE / "upload_journal.json"
    manifest_hash = digest_file(manifest_path)
    journal = json.loads(journal_path.read_text()) if journal_path.exists() else {
        "manifest_sha256": manifest_hash, "verified_objects": {},
    }
    if journal["manifest_sha256"] != manifest_hash:
        raise ValueError("Manifest changed; retain the old journal and start a new migration snapshot")
    with client() as http:
        response = http.get(BASE + "/bucket")
        require(response, {200})
        bucket = next((item for item in response.json() if item["id"] == BUCKET), None)
        if bucket is None:
            require(http.post(BASE + "/bucket", json={"id": BUCKET, "name": BUCKET, "public": False}), {200, 201})
        elif bucket.get("public") is not False:
            raise ValueError("Refusing to upload private evidence to a public bucket")
        objects = {}
        for row in manifest["records"]:
            path = Path(row["source_path"])
            if not path.is_file() or digest_file(path) != row["sha256"]:
                raise ValueError("Source changed since inventory: " + row["relative_path"])
            for part in row["parts"]:
                objects.setdefault(part["key"], (path, part))
        items = iter(objects.values())
        completed = 0
        print(json.dumps({"phase": "uploading", "objects": len(objects), "parallel_transfers": 3}), flush=True)
        with ThreadPoolExecutor(max_workers=3) as executor:
            pending = {}

            def submit():
                item = next(items, None)
                if item is None:
                    return
                path, part = item
                future = executor.submit(upload_part, http, path, part, part["key"] in journal["verified_objects"])
                pending[future] = part

            for _ in range(3):
                submit()
            try:
                while pending:
                    done, _ = wait(pending, return_when=FIRST_COMPLETED)
                    for future in done:
                        pending.pop(future)
                        key, digest = future.result()
                        journal["verified_objects"][key] = digest
                        completed += 1
                        if completed % 25 == 0 or completed == len(objects):
                            save(journal_path, journal)
                            print(json.dumps({"verified_objects": completed, "total": len(objects)}), flush=True)
                        submit()
            finally:
                save(journal_path, journal)
        remote_manifest = {**manifest, "records": [
            {k: v for k, v in row.items() if k not in {"source_path", "original_path"}}
            for row in manifest["records"]
        ]}
        blob = json.dumps(remote_manifest, sort_keys=True).encode()
        digest = hashlib.sha256(blob).hexdigest()
        part = {"key": "archive/manifests/" + digest + ".json", "length": len(blob), "sha256": digest}
        response = http.post(BASE + "/object/" + BUCKET + "/" + part["key"],
                             headers={"Content-Type": "application/json", "x-upsert": "true"}, content=blob)
        require(response, {200, 201})
        verify_object(http, part)
        save(STAGE / "storage_receipt.json", {"all_objects_verified": True, "manifest_key": part["key"],
                                            "objects": len(objects), "studio_cutover_verified": False,
                                            "local_files_deleted": False})


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["prepare", "transfer"])
    arguments = parser.parse_args()
    if arguments.operation == "prepare":
        prepare()
    else:
        transfer()

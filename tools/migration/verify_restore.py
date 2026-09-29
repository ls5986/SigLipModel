"""Prove multi-object backups can be restored before authorizing any local cleanup."""
import hashlib
import json
import sqlite3
import sys
import tempfile
from contextlib import closing
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import quote

sys.path.insert(0, str(Path(__file__).resolve().parent))
from migrate_storage import BASE, BUCKET, STAGE, client, require, save


def restore_record(http, row, destination):
    position = 0
    whole = hashlib.sha256()
    with destination.open("xb") as output:
        for part in row["parts"]:
            if part["offset"] != position:
                raise ValueError("Archive chunks are not contiguous")
            digest, length = hashlib.sha256(), 0
            with http.stream("GET", BASE + "/object/authenticated/" + BUCKET + "/"
                             + quote(part["key"], safe="/")) as response:
                require(response, {200})
                for chunk in response.iter_bytes():
                    length += len(chunk)
                    if length > part["length"]:
                        raise ValueError("Restored chunk exceeds declared length")
                    output.write(chunk)
                    digest.update(chunk)
                    whole.update(chunk)
            if length != part["length"] or digest.hexdigest() != part["sha256"]:
                raise ValueError("Restored chunk hash mismatch")
            position += length
    if position != row["bytes"] or whole.hexdigest() != row["sha256"]:
        raise ValueError("Restored file hash mismatch")
    if destination.suffix == ".json":
        json.loads(destination.read_text(encoding="utf-8"))
    if destination.suffix == ".sqlite3":
        with closing(sqlite3.connect("file:" + destination.as_posix() + "?mode=ro", uri=True)) as db:
            if db.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ValueError("Restored SQLite database failed integrity check")


def main():
    manifest = json.loads((STAGE / "file_manifest.json").read_text())
    journal = json.loads((STAGE / "upload_journal.json").read_text())
    requested = {
        "artifacts\\silver_heads.joblib",
        "data\\human_reviews.json",
        "data\\studio\\studio.sqlite3",
    }
    selected = [row for row in manifest["records"] if row["root"] == "pilot"
                and row["relative_path"] in requested]
    results = []
    with tempfile.TemporaryDirectory(prefix="restore-proof-", dir=STAGE) as directory, client() as http:
        for index, row in enumerate(selected):
            if not all(journal["verified_objects"].get(part["key"]) == part["sha256"] for part in row["parts"]):
                results.append({"path": row["relative_path"], "status": "awaiting_upload"})
                continue
            target = Path(directory) / (str(index) + Path(row["relative_path"]).suffix)
            restore_record(http, row, target)
            results.append({"path": row["relative_path"], "status": "restored_and_hash_verified",
                            "bytes": row["bytes"], "sha256": row["sha256"]})
    receipt = {"checked_at": datetime.now(UTC).isoformat(), "results": results,
               "complete_backup_verified": False, "cleanup_authorized": False}
    save(STAGE / "restore_sample_receipt.json", receipt)
    print(json.dumps(receipt), flush=True)


if __name__ == "__main__":
    main()

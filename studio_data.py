"""Local review-first dataset, explicit proposals and durable human annotation provenance."""

from __future__ import annotations

import hashlib
import io
import json
import math
import re
import sqlite3
import stat
import threading
import zipfile
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from uuid import uuid4

from PIL import Image

from pilot import FEATURE_ROOMS, FEATURES, ROOT, SOURCE, UnionFind, read_json

ROOMS = ["kitchen", "bathroom", "living", "bedroom", "exterior", "outdoor", "other"]
CONDITIONS = ["updated", "mixed", "dated", "rough", "major", "unknown"]


def now():
    return datetime.now(UTC).isoformat()


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").casefold())


def physical_key(prop):
    fields = prop.get("metadata") or prop
    parcel = fields.get("ParcelNumber") or fields.get("parcel_number")
    state = fields.get("StateOrProvince") or fields.get("state")
    unit = fields.get("UnitNumber") or fields.get("unit_number")
    address = prop.get("address") or fields.get("UnparsedAddress")
    postal = fields.get("PostalCode") or fields.get("postal_code")
    if parcel and state:
        return f"parcel:{normalize(state)}:{normalize(parcel)}:{normalize(unit)}"
    if address and postal:
        return f"address:{normalize(address)}:{str(postal)[:5]}:{normalize(unit)}"
    return "listing:" + str(prop.get("id") or prop.get("ListingKey"))


def valid_zip_path(name):
    if "\\" in name or "\x00" in name:
        raise ValueError("ZIP must use safe relative paths without backslashes")
    path = PurePosixPath(name)
    if path.is_absolute() or any(part in {"..", "."} for part in path.parts) or ":" in name:
        raise ValueError("Unsafe ZIP path")
    return path


def trim_metadata(data):
    forbidden = ("private", "password", "secret", "token", "showing", "lockbox", "occupant",
                 "ownerphone", "ownername", "contact", "email", "phone", "accesscode")
    return {str(k): v for k, v in data.items()
            if not any(word in normalize(k) for word in forbidden)
            and not str(k).lower().endswith("url")
            and isinstance(v, (str, int, float, bool, list, type(None)))}


class StudioStore:
    def __init__(self, root: Path = ROOT, source: Path = SOURCE, app=None):
        self.root, self.source, self.app = root, source, app
        self.folder = root / "data" / "studio"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.path = self.folder / "studio.sqlite3"
        self.lock = threading.RLock()
        with self.connect() as db:
            db.executescript("""
            CREATE TABLE IF NOT EXISTS properties (
              id TEXT PRIMARY KEY, physical_key TEXT NOT NULL, metadata TEXT NOT NULL,
              source TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS property_identity ON properties(physical_key);
            CREATE TABLE IF NOT EXISTS images (
              id TEXT PRIMARY KEY, property_id TEXT NOT NULL REFERENCES properties(id),
              path TEXT NOT NULL, sha256 TEXT NOT NULL, sequence INTEGER,
              split TEXT NOT NULL, group_id TEXT NOT NULL, source TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS image_property ON images(property_id);
            CREATE INDEX IF NOT EXISTS image_hash ON images(sha256);
            CREATE TABLE IF NOT EXISTS proposals (
              id TEXT PRIMARY KEY, image_id TEXT, property_id TEXT NOT NULL,
              source TEXT NOT NULL, payload TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE INDEX IF NOT EXISTS proposal_property ON proposals(property_id);
            CREATE TABLE IF NOT EXISTS reviews (
              kind TEXT NOT NULL, id TEXT NOT NULL, payload TEXT NOT NULL,
              revision INTEGER NOT NULL, PRIMARY KEY(kind,id)
            );
            CREATE TABLE IF NOT EXISTS review_history (
              sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT NOT NULL,id TEXT NOT NULL,
              previous TEXT, payload TEXT NOT NULL, revision INTEGER NOT NULL, at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS imports (
              id TEXT PRIMARY KEY, preview TEXT NOT NULL, manifest TEXT NOT NULL,
              archive_path TEXT NOT NULL, status TEXT NOT NULL, created_at TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
            """)

    @contextmanager
    def connect(self):
        db = sqlite3.connect(self.path, timeout=15)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def seed(self):
        with self.lock, self.connect() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='seed-v1'").fetchone():
                self._reconcile_groups(db)
                return
            records = read_json(self.source / "records.json")
            media = read_json(self.source / "media.json")["items"]
            visuals = read_json(self.source / "visual.json")["listings"]
            manifest = read_json(self.root / "data" / "manifest.json")
            group_by = {}
            image_by = {}
            hash_group = {}
            for item in manifest["images"]:
                for key in item["listing_keys"]:
                    group_by[key] = (item["group_id"], item["split"])
                for key in item["all_source_ids"]:
                    image_by[key] = item
                hash_group[item["sha256"]] = (item["group_id"], item["split"])
            unique_properties = {row["ListingKey"]: row for row in records["comps"] + records["properties"]}
            subjects = {row["ListingKey"] for row in records["properties"]}
            for key, row in unique_properties.items():
                metadata = trim_metadata(row)
                metadata["source_role"] = "subject" if key in subjects else "comp"
                metadata["photo_stage"] = "unknown"
                metadata["source_snapshot"] = records.get("retrieved_at")
                db.execute("INSERT OR IGNORE INTO properties VALUES (?,?,?,?,?)",
                           (key, physical_key(row), json.dumps(metadata),
                            "existing-evidence", now()))
            for item in media:
                if item.get("download_status") != "downloaded":
                    continue
                key, media_id = str(item["ListingKey"]), str(item["MediaKey"])
                if key not in unique_properties:
                    continue
                identifier = f"{key}:{media_id}"
                group, split = hash_group.get(item["sha256"], group_by.get(key, (key, "learning")))
                path = (self.source / item["local_path"]).resolve()
                if not path.is_relative_to((self.source / "media").resolve()) or not path.is_file():
                    continue
                db.execute("INSERT OR IGNORE INTO images VALUES (?,?,?,?,?,?,?,?,?)",
                           (identifier, key, str(path), item["sha256"], item.get("Order"),
                            split, group, "existing-evidence", now()))
            for key, visual in visuals.items():
                if key not in unique_properties or visual.get("status") != "analyzed":
                    continue
                self._proposal(db, f"seed-property:{key}", None, key, "Prior GPT draft", {
                    "condition_label": visual.get("label"), "summary": visual.get("summary"),
                    "coverage": {"kitchen": visual.get("kitchen_seen"),
                                 "bathroom": visual.get("bathroom_seen"),
                                 "living": visual.get("living_seen")},
                    "limitations": visual.get("limitations", []), "model": visual.get("model"),
                    "review_status": "machine_proposed", "photo_stage": "unknown",
                })
                for observation in visual.get("images", []):
                    image_id = f"{key}:{observation['media_id']}"
                    if not db.execute("SELECT 1 FROM images WHERE id=?", (image_id,)).fetchone():
                        continue
                    room = observation["room"]
                    room = "outdoor" if room in {"yard", "pool"} else (
                        room if room in ROOMS else "other"
                    )
                    features = {name: None for name in FEATURES}
                    for feature in visual.get("features", []):
                        if feature["feature"] in FEATURES and observation["media_id"] in feature.get("media_ids", []):
                            features[feature["feature"]] = feature.get("present")
                    self._proposal(db, f"seed-image:{image_id}", image_id, key, "Prior GPT draft", {
                        "room": room, "features": features,
                        "observations": [observation.get("observation", "")],
                        "model": visual.get("model"), "review_status": "machine_proposed",
                    })
            self._reconcile_groups(db)
            db.execute("INSERT INTO settings VALUES ('seed-v1',?)", (now(),))

    @staticmethod
    def _reconcile_groups(db):
        rows = db.execute(
            "SELECT i.id,i.group_id,i.split,i.sha256,p.physical_key "
            "FROM images i JOIN properties p ON p.id=i.property_id"
        ).fetchall()
        groups = UnionFind(sorted({row["group_id"] for row in rows}))
        seen = {}
        for row in rows:
            for identity in ("hash:" + row["sha256"], row["physical_key"]):
                if identity in seen:
                    groups.union(row["group_id"], seen[identity])
                seen[identity] = row["group_id"]
        protected = {groups.find(row["group_id"]) for row in rows if row["split"] == "test"}
        updates = []
        for row in rows:
            group = groups.find(row["group_id"])
            split = "test" if group in protected else row["split"]
            if group != row["group_id"] or split != row["split"]:
                updates.append((group, split, row["id"]))
        db.executemany("UPDATE images SET group_id=?,split=? WHERE id=?", updates)

    @staticmethod
    def _proposal(db, identifier, image_id, property_id, source, payload):
        db.execute("INSERT OR IGNORE INTO proposals VALUES (?,?,?,?,?,?)",
                   (identifier, image_id, property_id, source, json.dumps(payload), now()))

    def legacy(self):
        if self.app:
            return self.app.store.snapshot()
        path = self.root / "data" / "human_reviews.json"
        return read_json(path) if path.exists() else {}

    def reviews(self, db, kind, identifier, legacy):
        row = db.execute("SELECT payload,revision FROM reviews WHERE kind=? AND id=?",
                         (kind, identifier)).fetchone()
        if row:
            return {**json.loads(row["payload"]), "revision": row["revision"]}
        if kind == "property":
            data = dict(legacy.get("properties", {}).get(identifier, {}))
            data["legacy"] = bool(data)
            return {**data, "revision": 0}
        label = legacy.get("images", {}).get(identifier, {})
        correction = legacy.get("room_corrections", {}).get(identifier, {})
        preferences = [legacy.get(field, {}).get(identifier) for field in
                       ("room_preferences", "evaluation_preferences")]
        preferences = [value for value in preferences if value]
        pref = max(preferences, key=lambda r: r.get("updated_at", "")) if preferences else {}
        if pref.get("status") != "approved":
            pref = {}
        room = correction.get("room") if correction.get("status") == "approved" else label.get("room")
        return {
            "revision": 0, "room": room,
            "features": {name: bool(value) if value is not None else None
                         for name, value in label.get("features", {}).items()},
            "preference": pref.get("preference") if pref.get("room") == room or not room else None,
            "preference_room": pref.get("room"),
            "notes": label.get("notes", ""), "status": label.get("status", "unreviewed"),
            "reviewer": label.get("reviewer", correction.get("reviewer", pref.get("reviewer"))),
            "legacy": bool(label or correction or pref),
            "room_confirmed": bool(room and (label.get("status") == "approved" or
                                          correction.get("status") == "approved")),
            "label_scope": "Prior separate approvals; no whole-property target inferred",
        }

    def property(self, identifier, legacy=None):
        legacy = self.legacy() if legacy is None else legacy
        with self.connect() as db:
            prop = db.execute("SELECT * FROM properties WHERE id=?", (identifier,)).fetchone()
            if prop is None:
                raise ValueError("Unknown property")
            metadata = json.loads(prop["metadata"])
            proposals = db.execute("SELECT * FROM proposals WHERE property_id=? ORDER BY created_at",
                                   (identifier,)).fetchall()
            by_image, property_suggestions = {}, []
            for proposal in proposals:
                payload = {**json.loads(proposal["payload"]), "source": proposal["source"],
                           "proposal_id": proposal["id"]}
                if proposal["image_id"]:
                    by_image.setdefault(proposal["image_id"], []).append(payload)
                else:
                    property_suggestions.append(payload)
            images = []
            coverage = {room: "unknown" for room in ("kitchen", "bathroom", "living")}
            for row in db.execute("SELECT * FROM images WHERE property_id=? ORDER BY sequence,id",
                                  (identifier,)):
                proposals_for_image = by_image.get(row["id"], [])
                review = self.reviews(db, "image", row["id"], legacy)
                latest = proposals_for_image[-1] if proposals_for_image else {}
                local = None
                if self.app and row["id"] in self.app.rows:
                    candidate = self.app.candidate_predictions.get(row["id"], {})
                    local = {"room": candidate.get("room"),
                             "preferences": candidate.get("preferences", {}),
                             "version": self.app.candidate["version"] if self.app.candidate else None}
                if latest.get("source") == "local-siglip-draft":
                    score = latest.get("room_preference_score")
                    local = {"room": latest.get("room"),
                             "version": latest.get("model_version") or latest.get("run_id"),
                             "preferences": {latest.get("room"): {"score": score}}
                             if score is not None else {}}
                room = review.get("room") or latest.get("room") or (local or {}).get("room") or "other"
                room_confirmed = bool(review.get("room") and (review.get("status") == "approved"
                                                             or review.get("room_confirmed")))
                if room in coverage:
                    coverage[room] = "confirmed" if room_confirmed else (
                        coverage[room] if coverage[room] == "confirmed" else "suggested"
                    )
                features = {name: None for name in FEATURES}
                features.update(latest.get("features", {}))
                features.update(review.get("features", {}))
                warnings = []
                if not Path(row["path"]).is_file():
                    warnings.append("Original photo file missing")
                if row["split"] == "test":
                    warnings.append("Protected test group: reviews are evaluation-only")
                if not proposals_for_image:
                    warnings.append("No AI tags yet: local prelabeling can propose rooms/features")
                gpt = next((p for p in reversed(proposals_for_image)
                            if p.get("source") != "local-siglip-draft"), {})
                if local and gpt.get("room") and local.get("room") != gpt["room"]:
                    warnings.append("GPT and local room model disagree")
                images.append({
                    "id": row["id"], "property_id": identifier,
                    "room": room, "room_source": "Human approved" if room_confirmed else
                    latest.get("source", "Local model suggestion" if local else "Unknown"),
                    "features": features, "suggestions": proposals_for_image, "review": review,
                    "local_model": local, "warnings": warnings, "sha256": row["sha256"],
                    "split": row["split"], "sequence": row["sequence"],
                    "training_allowed": row["split"] != "test",
                    "address": metadata.get("UnparsedAddress", metadata.get("address", identifier)),
                })
            property_view = {
                "id": identifier, "address": metadata.get("UnparsedAddress", metadata.get("address", identifier)),
                "city": metadata.get("City", metadata.get("city")),
                "year_built": metadata.get("YearBuilt", metadata.get("year_built")),
                "property_type": metadata.get("PropertySubType", metadata.get("property_type")),
                "metadata": metadata, "review": self.reviews(db, "property", identifier, legacy),
            }
        return {"property": property_view, "images": images, "coverage": coverage,
                "property_suggestions": property_suggestions}

    def properties(self, search="", queue="all", offset=0, limit=24):
        if queue not in {"all", "needs_review", "disagreement", "missing_coverage", "reviewed"}:
            raise ValueError("Unsupported queue")
        offset, limit = max(0, int(offset)), min(48, max(1, int(limit)))
        with self.connect() as db:
            ids = [row["id"] for row in db.execute("SELECT id FROM properties ORDER BY created_at,id")]
        # Metadata is bounded and images are lazy; no photo bytes or giant bootstrap payload.
        items = []
        legacy = self.legacy()
        for identifier in ids:
            info = self.property(identifier, legacy)
            prop, images = info["property"], info["images"]
            if search and search.casefold() not in " ".join(
                str(prop.get(key) or "") for key in ("id", "address", "city", "property_type")
            ).casefold():
                continue
            status = prop["review"].get("status", "unreviewed")
            reviewed = sum(row["review"].get("status") == "approved" for row in images)
            disagreement = any("disagree" in warning for row in images for warning in row["warnings"])
            missing = any(value == "unknown" for value in info["coverage"].values())
            if (queue == "reviewed" and status != "approved") or (
                queue == "needs_review" and status == "approved" and reviewed == len(images)
            ) or (queue == "disagreement" and not disagreement) or (
                queue == "missing_coverage" and not missing
            ):
                continue
            signals = []
            if disagreement:
                signals.append("Room disagreement")
            if missing:
                signals.append("Missing key-room coverage")
            if status != "approved":
                signals.append("Property decision needed")
            items.append({**{key: prop[key] for key in
                             ("id", "address", "city", "year_built", "property_type")},
                          "image_count": len(images), "reviewed_images": reviewed,
                          "review_status": status, "target_fit": prop["review"].get("target_fit"),
                          "coverage": info["coverage"], "signals": signals,
                          "source_role": prop["metadata"].get("source_role", "imported"),
                          "hero_image_id": images[0]["id"] if images else None})
        # Incomplete/disagreeing cases first; not exclusively high local-model scores.
        if queue == "needs_review":
            items.sort(key=lambda row: (row["source_role"] == "comp",
                                       -int("Room disagreement" in row["signals"]),
                                       -int(row["review_status"] != "approved"), row["id"]))
        return {"items": items[offset:offset + limit], "total": len(items),
                "offset": offset, "limit": limit}

    def image_list(self, room="", status="all", offset=0, limit=24, score_above=None):
        if room and room not in ROOMS or status not in {"all", "unreviewed"}:
            raise ValueError("Unsupported image room or review filter")
        if score_above is not None:
            score_above = float(score_above)
            if not math.isfinite(score_above) or not 0 <= score_above <= 1:
                raise ValueError("Score filter must be between zero and one")
        result = []
        legacy = self.legacy()
        with self.connect() as db:
            keys = [row["id"] for row in db.execute("SELECT id FROM properties ORDER BY id")]
        for key in keys:
            for image in self.property(key, legacy)["images"]:
                if room and image["room"] != room:
                    continue
                if status == "unreviewed" and image["review"].get("status") == "approved":
                    continue
                if score_above is not None:
                    score = (image.get("local_model") or {}).get("preferences", {}).get(image["room"], {})
                    score = score.get("score") if isinstance(score, dict) else score
                    if type(score) not in {int, float} or not score > score_above:
                        continue
                result.append(image)
        offset, limit = max(0, int(offset)), min(48, max(1, int(limit)))
        return {"items": result[offset:offset + limit], "total": len(result),
                "offset": offset, "limit": limit}

    def save_review(self, payload):
        kind, identifier = payload.get("kind"), payload.get("id")
        if kind not in {"image", "property"}:
            raise ValueError("Unsupported review type")
        if not isinstance(identifier, str):
            raise ValueError("Review requires a known ID")
        reviewer = payload.get("reviewer")
        if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 100:
            raise ValueError("Enter reviewer name or initials once")
        status = payload.get("status")
        if status not in {"draft", "approved"}:
            raise ValueError("Choose draft or approved")
        record = {"status": status, "reviewer": reviewer.strip(), "updated_at": now(),
                  "source": "human", "notes": payload.get("notes", "")}
        if not isinstance(record["notes"], str) or len(record["notes"]) > 4000:
            raise ValueError("Notes too long")
        with self.lock, self.connect() as db:
            table = "images" if kind == "image" else "properties"
            source = db.execute(f"SELECT * FROM {table} WHERE id=?", (identifier,)).fetchone()
            if source is None:
                raise ValueError("Unknown review item")
            if kind == "image":
                room = payload.get("room")
                features = payload.get("features", {})
                preference = payload.get("preference")
                if room not in ROOMS:
                    raise ValueError("Choose the actual room or other")
                if not isinstance(features, dict) or set(features) - set(FEATURES):
                    raise ValueError("Invalid visible feature labels")
                if any(value is not None and type(value) is not bool for value in features.values()):
                    raise ValueError("Features require true, false or null (unknown)")
                if any(value is True and feature in FEATURE_ROOMS and room not in FEATURE_ROOMS[feature]
                       for feature, value in features.items()):
                    raise ValueError("A present feature conflicts with the room; change it to Unknown or correct the room")
                if preference not in {None, "target", "not_target", "unsure"}:
                    raise ValueError("Invalid room preference")
                context = payload.get("context")
                if context is not None and context not in {"subject", "shared_amenity", "floor_plan", "unrelated", "unknown"}:
                    raise ValueError("Invalid photo context")
                if context in {"shared_amenity", "floor_plan", "unrelated"} and (
                    preference is not None or any(value is not None for value in features.values())
                ):
                    raise ValueError("Non-subject photos cannot approve subject condition or work preference labels")
                record.update(room=room, features=features, preference=preference,
                              training_allowed=source["split"] != "test",
                              property_target_inferred=False)
                if context is not None:
                    record["context"] = context
            else:
                target, condition = payload.get("target_fit"), payload.get("condition_label")
                reason = payload.get("reason", "")
                if target not in {None, "target", "not_target", "unsure"} or condition not in [
                    None, *CONDITIONS,
                ]:
                    raise ValueError("Invalid property decision or condition label")
                if not isinstance(reason, str) or len(reason) > 2000:
                    raise ValueError("Invalid reason")
                if status == "approved" and (target is None or not reason.strip()):
                    raise ValueError("Choose Target / Not target / Unsure and give a short reason")
                record.update(target_fit=target, condition_label=condition, reason=reason,
                              image_labels_approved=False)
            previous = db.execute("SELECT payload,revision FROM reviews WHERE kind=? AND id=?",
                                  (kind, identifier)).fetchone()
            revision = previous["revision"] if previous else 0
            if kind == "image" and previous and "context" not in record:
                prior_context = json.loads(previous["payload"]).get("context")
                if prior_context is not None:
                    record["context"] = prior_context
            if kind == "image" and record.get("context") in {"shared_amenity", "floor_plan", "unrelated"} and (
                record["preference"] is not None or any(v is not None for v in record["features"].values())
            ):
                raise ValueError("Non-subject photo context conflicts with subject condition labels")
            if payload.get("expected_revision") != revision:
                raise RuntimeError("This record changed in another window. Reload before saving.")
            db.execute("INSERT OR REPLACE INTO reviews VALUES (?,?,?,?)",
                       (kind, identifier, json.dumps(record), revision + 1))
            db.execute("INSERT INTO review_history(kind,id,previous,payload,revision,at) "
                       "VALUES (?,?,?,?,?,?)", (kind, identifier, previous["payload"] if previous else None,
                                                json.dumps(record), revision + 1, now()))
        return {**record, "revision": revision + 1}

    def image_path(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT path FROM images WHERE id=?", (identifier,)).fetchone()
        if row is None:
            raise ValueError("Unknown image")
        path = Path(row["path"]).resolve()
        if not any(path.is_relative_to(base.resolve()) for base in
                   (self.source / "media", self.folder / "assets")) or not path.is_file():
            raise ValueError("Image file unavailable")
        return path

    def apply_proposals(self, proposals):
        count = 0
        with self.lock, self.connect() as db:
            for bundle in proposals:
                property_id = bundle["property_id"]
                if not db.execute("SELECT 1 FROM properties WHERE id=?", (property_id,)).fetchone():
                    raise ValueError("Proposal property missing")
                base = f"{bundle['run_id']}:{bundle.get('arm', 'local')}:{bundle['prompt_hash']}:{property_id}"
                meta = {key: bundle.get(key) for key in
                        ("run_id", "arm", "model", "model_version", "prompt_hash", "source")}
                for image in bundle.get("images", []):
                    identifier = image.get("image_id") or image.get("id")
                    if not db.execute("SELECT 1 FROM images WHERE id=? AND property_id=?",
                                      (identifier, property_id)).fetchone():
                        raise ValueError("Proposal image belongs to a different property")
                    payload = {**image, **meta, "review_status": "machine_proposed"}
                    self._proposal(db, f"{base}:{identifier}", identifier, property_id,
                                   bundle.get("source") or "GPT draft", payload)
                    count += 1
                self._proposal(db, f"{base}:property", None, property_id, bundle.get("source") or "GPT draft",
                               {**bundle.get("property", {}), **meta, "review_status": "machine_proposed"})
        return {"status": "applied_as_drafts", "image_proposals": count,
                "human_reviews_changed": False}

    def summary(self):
        legacy = self.legacy()
        with self.connect() as db:
            properties = [row["id"] for row in db.execute("SELECT id FROM properties")]
            images = db.execute("SELECT id,property_id,path FROM images").fetchall()
            statuses = {(row["kind"], row["id"]): row["status"] for row in db.execute(
                "SELECT kind,id,json_extract(payload,'$.status') AS status FROM reviews"
            )}

        def approved(kind, identifier):
            key = (kind, identifier)
            if key in statuses:
                return statuses[key] == "approved"
            field = "images" if kind == "image" else "properties"
            return legacy.get(field, {}).get(identifier, {}).get("status") == "approved"

        pending = {key for key in properties if not approved("property", key)}
        image_approvals = 0
        missing_images = 0
        for image in images:
            is_approved = approved("image", image["id"])
            image_approvals += is_approved
            if not is_approved:
                pending.add(image["property_id"])
            missing_images += not Path(image["path"]).is_file()
        counts = {
            "properties": len(properties), "images": len(images),
            "human_image_reviews": image_approvals,
            "human_property_reviews": sum(approved("property", key) for key in properties),
            "pending_properties": len(pending), "missing_images": missing_images,
        }
        return {
            "counts": counts, "existing_reviews": {
                "images": len(legacy.get("images", {})), "properties": len(legacy.get("properties", {})),
                "preferences": len(legacy.get("room_preferences", {})),
            },
            "learning_rules": [
                "AI tags are editable proposals until explicitly approved.",
                "Room/feature facts, photo work preference, property target and financial success are separate.",
                "An updated kitchen never by itself establishes whole-property turnkey.",
                "Unknown is not absent; repeated photo angles do not create independent properties.",
                "Test-group reviews stay evaluation-only; no silent split promotion in Studio.",
                "Prompt outputs and local-model scores are not human accuracy or profit.",
            ],
        }

    @staticmethod
    def import_template():
        return {
            "dataset_name": "My acquisition examples",
            "properties": [{"id": "your-listing-key", "address": "Example address",
                            "metadata": {"PropertySubType": "Condominium", "YearBuilt": 1980,
                                         "ParcelNumber": "parcel-id", "UnitNumber": "A",
                                         "StateOrProvince": "CA", "PostalCode": "92101"},
                            "photo_stage": "unknown"}],
            "images": [{"id": "your-media-id", "property_id": "your-listing-key",
                        "path": "images/kitchen.jpg", "order": 1,
                        "proposed_room": "kitchen", "proposed_features": {"old_cabinetry": None}}],
            "instructions": "ZIP contains dataset.json and referenced images. Relative paths only. "
            "Proposed tags remain machine drafts, never approved labels.",
        }

    def preview_import(self, archive: Path):
        token = uuid4().hex
        saved = self.folder / "imports" / token
        saved.mkdir(parents=True)
        destination = saved / "upload.zip"
        archive.replace(destination)
        errors, warnings, images, properties = [], [], [], []
        duplicates = 0
        try:
            with zipfile.ZipFile(destination) as zip_file:
                infos = zip_file.infolist()
                if len(infos) > 20000 or sum(info.file_size for info in infos) > 2_000_000_000:
                    raise ValueError("Archive exceeds 20,000 entries or 2 GB uncompressed")
                names = set()
                for info in infos:
                    valid_zip_path(info.filename)
                    if info.filename in names:
                        raise ValueError("Duplicate archive member path")
                    names.add(info.filename)
                    if stat.S_ISLNK(info.external_attr >> 16):
                        raise ValueError("Archive symlinks are not accepted")
                    if info.flag_bits & 1:
                        raise ValueError("Encrypted archives are not supported")
                if "dataset.json" not in names:
                    raise ValueError("ZIP must include dataset.json at its root")
                if zip_file.getinfo("dataset.json").file_size > 10_000_000:
                    raise ValueError("Dataset manifest exceeds 10 MB")
                manifest = json.loads(zip_file.read("dataset.json"))
                if not isinstance(manifest, dict):
                    raise ValueError("Dataset manifest must be a JSON object")
                props = manifest.get("properties", [])
                media = manifest.get("images", [])
                if not isinstance(props, list) or not isinstance(media, list) or not 1 <= len(props) <= 2000:
                    raise ValueError("Provide 1–2,000 properties and an images array per import")
                if not 1 <= len(media) <= 15000:
                    raise ValueError("Provide 1–15,000 image records")
                prop_ids = set()
                for prop in props:
                    if not isinstance(prop, dict) or not isinstance(prop.get("metadata", {}), dict):
                        raise ValueError("Each property and its metadata must be an object")
                    key = prop.get("id") or prop.get("ListingKey") or ""
                    if not isinstance(key, str) or not key or len(key) > 255 or key in prop_ids or ":" in key:
                        raise ValueError("Property IDs must be unique nonempty strings up to 255 characters")
                    prop_ids.add(key)
                    metadata = trim_metadata(prop.get("metadata", {}))
                    metadata.update({
                        "UnparsedAddress": prop.get("address", metadata.get("UnparsedAddress", key)),
                        "photo_stage": prop.get("photo_stage", "unknown"),
                        "import_dataset": str(manifest.get("dataset_name", "Imported dataset"))[:200],
                    })
                    properties.append({"id": key, "metadata": metadata,
                                       "physical_key": physical_key({"id": key, "metadata": metadata})})
                image_ids = set()
                for entry in media:
                    if not isinstance(entry, dict):
                        raise ValueError("Each image must be an object")
                    key = entry.get("property_id", "")
                    identifier = entry.get("id") or ""
                    relative = entry.get("path")
                    if (not isinstance(key, str) or not isinstance(identifier, str)
                            or key not in prop_ids or not identifier or len(identifier) > 255 or ":" in identifier):
                        raise ValueError("Every image needs an ID and matching property_id")
                    order = entry.get("order")
                    if order is not None and (type(order) is not int or not 0 <= order <= 100000):
                        raise ValueError("Image order must be an integer between 0 and 100000")
                    image_id = f"{key}:{identifier}"
                    if image_id in image_ids:
                        raise ValueError("Duplicate property/media ID")
                    image_ids.add(image_id)
                    if not isinstance(relative, str):
                        raise ValueError("Image paths must be relative archive strings")
                    valid_zip_path(relative)
                    if relative not in names or not 0 < zip_file.getinfo(relative).file_size <= 6_000_000:
                        raise ValueError(f"Missing or oversized image: {relative[:100]}")
                    blob = zip_file.read(relative)
                    with Image.open(io.BytesIO(blob)) as photo:
                        if photo.format not in {"JPEG", "PNG", "WEBP"}:
                            raise ValueError("Only JPEG/PNG/WebP photos are accepted")
                        if photo.width * photo.height > 40_000_000:
                            raise ValueError("Image pixel dimensions exceed safety limit")
                        photo.verify()
                    digest = hashlib.sha256(blob).hexdigest()
                    room = entry.get("proposed_room")
                    if room is not None and room not in ROOMS:
                        raise ValueError("Unsupported proposed room")
                    features = entry.get("proposed_features", {})
                    if not isinstance(features, dict) or set(features) - set(FEATURES) or any(
                        value is not None and type(value) is not bool for value in features.values()
                    ):
                        raise ValueError("Proposed features must use known true/false/null tags")
                    with self.connect() as db:
                        old_id = db.execute("SELECT sha256 FROM images WHERE id=?", (image_id,)).fetchone()
                        if old_id and old_id["sha256"] != digest:
                            raise ValueError("Existing media ID has different bytes; use a new snapshot/media ID")
                        duplicate = db.execute("SELECT 1 FROM images WHERE sha256=?", (digest,)).fetchone()
                    duplicates += int(duplicate is not None)
                    images.append({"id": image_id, "property_id": key, "path": relative,
                                   "sha256": digest, "sequence": entry.get("order"),
                                   "room": room, "features": features})
        except (ValueError, KeyError, TypeError, zipfile.BadZipFile, OSError,
                Image.DecompressionBombError) as exc:
            errors.append(str(exc)[:400])
        if duplicates:
            warnings.append(f"{duplicates} images duplicate existing bytes; they must share split groups.")
        warnings += [
            "Imported labels are draft proposals, not verified success or approved visual labels.",
            "Photo timing and licensed training rights must be checked before use.",
            "New images need local embeddings or explicit GPT prelabeling before model-assisted review.",
            "New imports match exact bytes and property identity; resized/edited near-duplicates need identity review.",
        ]
        preview = {"id": token, "properties": len(properties), "images": len(images),
                   "duplicates": duplicates, "warnings": warnings, "errors": errors,
                   "can_commit": not errors}
        with self.connect() as db:
            db.execute("INSERT INTO imports VALUES (?,?,?,?,?,?)",
                       (token, json.dumps(preview), json.dumps({"properties": properties, "images": images}),
                        str(destination), "preview", now()))
        return preview

    def commit_import(self, identifier):
        with self.lock, self.connect() as db:
            record = db.execute("SELECT * FROM imports WHERE id=?", (identifier,)).fetchone()
            if record is None:
                raise ValueError("Unknown import preview")
            if record["status"] == "committed":
                return {"status": "committed", "id": identifier, "already_committed": True}
            if not json.loads(record["preview"])["can_commit"]:
                raise ValueError("Resolve import errors before committing")
            manifest = json.loads(record["manifest"])
            with zipfile.ZipFile(record["archive_path"]) as archive:
                for prop in manifest["properties"]:
                    existing = db.execute("SELECT physical_key FROM properties WHERE id=?",
                                          (prop["id"],)).fetchone()
                    if existing and existing["physical_key"] != prop["physical_key"]:
                        raise ValueError("Existing listing has a different property identity; retain its metadata or use a new listing ID")
                    db.execute("INSERT OR IGNORE INTO properties VALUES (?,?,?,?,?)",
                               (prop["id"], prop["physical_key"], json.dumps(prop["metadata"]),
                                f"import:{identifier}", now()))
                for item in manifest["images"]:
                    existing = db.execute("SELECT sha256 FROM images WHERE id=?", (item["id"],)).fetchone()
                    if existing and existing["sha256"] != item["sha256"]:
                        raise ValueError("Media ID changed since preview; create a fresh preview")
                    blob = archive.read(item["path"])
                    if hashlib.sha256(blob).hexdigest() != item["sha256"]:
                        raise ValueError("Previewed image bytes changed")
                    with Image.open(io.BytesIO(blob)) as image:
                        suffix = {"JPEG": ".jpg", "PNG": ".png", "WEBP": ".webp"}[image.format]
                    asset = self.folder / "assets" / f"{item['sha256']}{suffix}"
                    asset.parent.mkdir(exist_ok=True)
                    if not asset.exists():
                        temporary = asset.with_suffix(suffix + ".tmp")
                        temporary.write_bytes(blob)
                        temporary.replace(asset)
                    elif hashlib.sha256(asset.read_bytes()).hexdigest() != item["sha256"]:
                        raise ValueError("Existing imported photo bytes changed; restore the original before committing")
                    prop = db.execute("SELECT physical_key FROM properties WHERE id=?",
                                      (item["property_id"],)).fetchone()
                    matches = db.execute(
                        "SELECT i.group_id,i.split FROM images i JOIN properties p "
                        "ON p.id=i.property_id WHERE i.sha256=? OR p.physical_key=?",
                        (item["sha256"], prop["physical_key"]),
                    ).fetchall()
                    split = "test" if any(row["split"] == "test" for row in matches) else (
                        "validation" if any(row["split"] == "validation" for row in matches)
                        else "learning"
                    )
                    group = min(row["group_id"] for row in matches) if matches else prop["physical_key"]
                    db.execute("INSERT OR IGNORE INTO images VALUES (?,?,?,?,?,?,?,?,?)",
                               (item["id"], item["property_id"], str(asset), item["sha256"],
                                item["sequence"], split, group, f"import:{identifier}", now()))
                    if item["room"] or item["features"]:
                        self._proposal(db, f"import:{identifier}:{item['id']}", item["id"],
                                       item["property_id"], "Imported draft",
                                       {"room": item["room"], "features": item["features"],
                                        "review_status": "machine_proposed"})
            self._reconcile_groups(db)
            db.execute("UPDATE imports SET status='committed' WHERE id=?", (identifier,))
        return {"status": "committed", "id": identifier, "properties": len(manifest["properties"]),
                "images": len(manifest["images"]), "human_approvals_created": 0}

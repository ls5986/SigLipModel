"""Expose collected historical candidates without turning cohort labels into approvals."""
import json
from pathlib import Path

from pilot import ROOT, read_json
from studio_data import StudioStore, now, physical_key, trim_metadata

COHORT = ROOT / "historical_samples" / "2026-09-27" / "full_cohort"


class HistoricalStore(StudioStore):
    def property(self, identifier, legacy=None):
        detail = super().property(identifier, legacy)
        if not hasattr(self, "_photo_contexts"):
            self._photo_contexts = {}
            path = COHORT / "sample_evidence.json"
            if path.exists():
                for row in read_json(path)["properties"]:
                    for group in row["photo_sets"]:
                        for media in group["metadata"]:
                            description = str(media.get("LongDescription") or media.get("ShortDescription") or "")
                            text = description.casefold()
                            context = "shared_amenity" if any(word in text for word in (
                                "community room", "community exercise", "community pool", "hoa", "clubhouse",
                            )) else "floor_plan" if "floor plan" in text or "floorplan" in text else None
                            self._photo_contexts[str(group["listing_key"]) + ":" + str(media.get("MediaKey"))] = (context, description)
        for image in detail["images"]:
            context, description = self._photo_contexts.get(image["id"], (None, ""))
            image.update(provider_context=context, provider_description=description)
        return detail

    def seed(self):
        super().seed()
        evidence_path = COHORT / "sample_evidence.json"
        if not evidence_path.exists():
            return
        with self.lock, self.connect() as db:
            if db.execute("SELECT 1 FROM settings WHERE key='historical-cohort-v1'").fetchone():
                return
            data = read_json(evidence_path)
            if not data.get("finished_at"):
                raise ValueError("Historical collection must finish before import")
            seen = set()
            for row in data["properties"]:
                key = row.get("candidate_listing_key")
                if not key or key in seen:
                    continue
                seen.add(key)
                listing = next(c["listing"] for c in row["candidates"] if c["listing"]["ListingKey"] == key)
                metadata = trim_metadata(listing)
                metadata.update(source_role="historical candidate", reference_label="GOOD EXAMPLE",
                                reference_scope="property cohort only; not human-approved photo labels",
                                photo_stage="acquisition_candidate")
                db.execute("INSERT OR IGNORE INTO properties VALUES (?,?,?,?,?)", (
                    key, physical_key(listing), json.dumps(metadata), "historical-cohort", now(),
                ))
                for group in row["photo_sets"]:
                    if group["purpose"] != "acquisition_candidate":
                        continue
                    for photo in group["photos"]:
                        if photo["status"] != "downloaded":
                            continue
                        path = (COHORT / photo["path"]).resolve()
                        if not path.is_relative_to((COHORT / "originals").resolve()) or not path.is_file():
                            raise ValueError("Historical photo is not available at the approved source")
                        image_id = str(key) + ":" + str(photo["media_key"])
                        db.execute("INSERT OR IGNORE INTO images VALUES (?,?,?,?,?,?,?,?,?)", (
                            image_id, key, str(path), photo["sha256"], photo["order"], "learning",
                            physical_key(listing), "historical-cohort", now(),
                        ))
            self._reconcile_groups(db)
            db.execute("INSERT INTO settings VALUES ('historical-cohort-v1',?)", (now(),))

    def image_path(self, identifier):
        with self.connect() as db:
            row = db.execute("SELECT path FROM images WHERE id=?", (identifier,)).fetchone()
        if row:
            path = Path(row["path"]).resolve()
            if path.is_relative_to((COHORT / "originals").resolve()) and path.is_file():
                return path
        return super().image_path(identifier)

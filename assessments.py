"""Explicit single-call GPT drafts and separate historical-photo verification."""
import copy
import hashlib
import json
import threading
from datetime import UTC, datetime

from acquisition_policy import audit_evidence
from pilot import ROOT, read_json, write_json
from prompt_lab import RESPONSE_SCHEMA, ROOM_FIRST_TEXT

POLICY = """Propose visual renovation target fit, ignoring price or financial outcomes.
Target means meaningful dated/cosmetic renovation scope remains in the SUBJECT HOME.
An updated kitchen does not negate dated bathrooms or other meaningful remaining work.
Do not infer subject condition from shared HOA amenities, floor plans or unrelated images.
Use unsure for missing key-room evidence, uncertain subject/amenity context or unclear scope.
Return target_fit (target/not_target/unsure) and target_reason.
For every image return context (subject/shared_amenity/floor_plan/unknown) and
renovation_scope_score (0..1, or null when not observable). These are uncalibrated
GPT estimates of visible scope, not trained preference probabilities or profit.
Do not use the acquisition cohort label as evidence. No hidden-condition claims."""


class Assessments:
    def __init__(self, studio, folder=None):
        self.studio = studio
        self.folder = folder or ROOT / "data" / "assessment_jobs"
        self.folder.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.thread = None
        self._history_cache = None
        self._acquisition_audit = None
        for path in self.folder.glob("*.json"):
            job = read_json(path)
            if job.get("status") == "running":
                job.update(status="interrupted", error="Server restarted; no automatic retry")
                write_json(path, job)

    def job(self, identifier):
        if not isinstance(identifier, str) or len(identifier) != 32 or any(c not in "0123456789abcdef" for c in identifier):
            raise ValueError("Invalid assessment ID")
        return read_json(self.folder / (identifier + ".json"))

    def save(self, job):
        write_json(self.folder / (job["id"] + ".json"), job)

    def latest(self, identifier):
        jobs = [read_json(p) for p in self.folder.glob("*.json")]
        return max((j for j in jobs if j["property_id"] == identifier),
                   key=lambda j: j["created_at"], default=None)

    def history(self, identifier=None):
        path = ROOT / "historical_samples" / "2026-09-27" / "full_cohort" / "sample_evidence.json"
        if not path.exists():
            return None
        version = path.stat().st_mtime_ns
        if self._history_cache is None or self._history_cache[0] != version:
            data = read_json(path)
            self._acquisition_audit = audit_evidence(data)
            selected = {}
            for row in data["properties"]:
                key = row.get("candidate_listing_key")
                if key:
                    selected.setdefault(str(key), []).append({
                        "source_row": row["source_row"], "source": row["source"],
                        "photo_sets": [{"purpose": g["purpose"], "photos": [
                            {"sha256": p.get("sha256"), "status": p["status"]} for p in g["photos"]
                        ]} for g in row["photo_sets"]],
                    })
            self._history_cache = (version, selected, len(data["properties"]))
        _, selected, row_count = self._history_cache
        if identifier is None:
            return {"source_rows": row_count, "matched_rows": sum(len(v) for v in selected.values()),
                    "matched_listings": len(selected), "photo_listings": sum(
                        any(p["status"] == "downloaded" for g in rows[0]["photo_sets"] for p in g["photos"])
                        for rows in selected.values()),
                    "acquisition_policy_counts": self._acquisition_audit["listing_counts"],
                    "notice": "Prior acquisitions only. Last-sale-only matches are quarantined, not trainable."}
        rows = selected.get(str(identifier), [])
        if not rows:
            return None
        group = next((g for g in rows[0]["photo_sets"] if g["purpose"] == "acquisition_candidate"), None)
        identity = hashlib.sha256(json.dumps(sorted(p["sha256"] for p in group["photos"]
                                                   if p["status"] == "downloaded")).encode()).hexdigest() if group else None
        file = self.folder / "era_reviews" / (hashlib.sha256(str(identifier).encode()).hexdigest() + ".json")
        review = read_json(file) if file.exists() else None
        policy = self._acquisition_audit["listings"][str(identifier)]
        rejected = bool(review and review["decision"] == "wrong_era")
        blocked = rejected or policy["status"] != "prior_acquisition_candidate"
        return {"source_rows": [r["source_row"] for r in rows], "source": rows[0]["source"],
                "evidence_hash": identity, "review": review,
                "acquisition_status": "wrong_era" if rejected else policy["status"],
                "blocked": blocked,
                "block_reason": "You flagged these photos as the wrong era/property." if rejected else policy["block_reason"],
                "mls_listing": policy["listing"],
                "timing_verified": bool(review and review["decision"] == "correct_era"
                                        and review["evidence_hash"] == identity and not blocked),
                "trainable": False, "training_gate": "Photo context, duplicates and protected split checks still required"}

    def review_era(self, payload):
        identifier = payload.get("property_id")
        detail = self.studio.store.property(identifier)
        history = self.history(identifier)
        if history is None:
            raise ValueError("No spreadsheet acquisition candidate is attached to this listing")
        if payload.get("decision") == "correct_era" and history.get("acquisition_status") == "needs_prior_listing":
            raise ValueError("This listing does not match the prior acquisition; rematch its listing before approving it")
        reviewer, reason = payload.get("reviewer"), payload.get("reason")
        if not isinstance(reviewer, str) or not reviewer.strip() or not isinstance(reason, str) or not reason.strip():
            raise ValueError("Reviewer and evidence reason required")
        if len(reviewer) > 100 or len(reason) > 2000:
            raise ValueError("Reviewer or reason too long")
        if payload.get("decision") not in {"correct_era", "wrong_era", "unsure"}:
            raise ValueError("Invalid photo-era decision")
        if payload.get("evidence_hash") != history["evidence_hash"]:
            raise ValueError("Photo evidence changed; reload before verifying")
        # The review must refer to all downloaded candidate photos, not a different subset.
        actual_hashes = sorted(image["sha256"] for image in detail["images"])
        actual_hash = hashlib.sha256(json.dumps(actual_hashes).encode()).hexdigest()
        if actual_hash != history["evidence_hash"]:
            raise ValueError("This Studio photo set differs from the collected historical set. Reconcile it before confirming the era.")
        with self.lock:
            current = self.history(identifier)["review"]
            if payload.get("expected_revision") != (current or {}).get("revision", 0):
                raise RuntimeError("Photo-era review changed in another window")
            record = {"property_id": identifier, "decision": payload["decision"], "reviewer": reviewer.strip(),
                      "reason": reason.strip(), "evidence_hash": history["evidence_hash"],
                      "revision": (current or {}).get("revision", 0) + 1,
                      "at": datetime.now(UTC).isoformat(),
                      "history": (current or {}).get("history", []) + ([{k: v for k, v in current.items() if k != "history"}] if current else [])}
            path = self.folder / "era_reviews" / (hashlib.sha256(str(identifier).encode()).hexdigest() + ".json")
            write_json(path, record)
            return record

    def preview(self, payload):
        identifier = payload.get("property_id")
        history = self.history(identifier)
        if history and history.get("blocked"):
            raise ValueError(history["block_reason"])
        self.studio.store.property(identifier)
        preview = self.studio.prompts.preview({
            "property_ids": [identifier], "baseline_id": "legacy-any-update-v1",
            "candidate_text": ROOM_FIRST_TEXT.replace(" or acquisition suitability", "") + "\n" + POLICY,
            "model": "gpt-4.1-mini", "images_per_property": 8,
        })
        job = {"id": preview["id"], "property_id": identifier, "status": "preview",
               "created_at": datetime.now(UTC).isoformat(), "planned_calls": 1,
               "image_count": preview["image_count"], "selected_images": preview["selected_images"],
               "estimated_cost": None, "warnings": preview["warnings"] + [
                   "At most eight selected photos, not an all-photo assessment.",
                   "Shared-amenity or insufficient room evidence forces manual review.",
                   "Comp/economics integration is not connected here; final sourcing decision stays NEEDS REVIEW.",
               ], "prediction": None}
        self.save(job)
        return job

    def start(self, payload):
        if payload.get("approved") is not True:
            raise ValueError("Explicit paid-call approval required")
        with self.lock:
            job = self.job(payload.get("id"))
            if job["status"] != "preview":
                return job
            history = self.history(job["property_id"])
            if history and history.get("blocked"):
                raise ValueError(history["block_reason"])
            if self.thread and self.thread.is_alive():
                raise RuntimeError("An assessment is already running")
            job["status"] = "running"
            self.save(job)
            self.thread = threading.Thread(target=self.run, args=(job,), daemon=True)
            self.thread.start()
            return job

    @staticmethod
    def validate(raw, frozen, lab):
        if not isinstance(raw, dict) or set(raw) != set(RESPONSE_SCHEMA["properties"]) | {"target_fit", "target_reason"}:
            raise ValueError("Assessment keys do not match schema")
        if raw["target_fit"] not in {"target", "not_target", "unsure"} or not isinstance(raw["target_reason"], str) or not raw["target_reason"].strip():
            raise ValueError("Invalid target judgment")
        base = {k: copy.deepcopy(v) for k, v in raw.items() if k in RESPONSE_SCHEMA["properties"]}
        if not isinstance(base.get("images"), list):
            raise ValueError("Invalid image records")
        uncertain_context = False
        for image in base["images"]:
            if not isinstance(image, dict):
                raise ValueError("Invalid photo assessment")
            context = image.pop("context", None)
            score = image.pop("renovation_scope_score", "missing")
            if context not in {"subject", "shared_amenity", "floor_plan", "unknown"}:
                raise ValueError("Invalid image context")
            if score is not None and (type(score) not in {int, float} or not 0 <= score <= 1):
                raise ValueError("Invalid image scope score")
            if context != "subject":
                uncertain_context = True
                if score is not None:
                    raise ValueError("Non-subject photo cannot have renovation scope score")
        result = lab._validate(base, frozen, "candidate")
        if not result["accepted"]:
            raise ValueError("Candidate evidence policy failed: " + "; ".join(result["errors"])[:500])
        reasons = []
        if not all(base["coverage"].values()):
            reasons.append("Missing kitchen/bathroom/living coverage")
        if uncertain_context:
            reasons.append("Selected set includes non-subject or uncertain-context images")
        if base["condition_label"] == "UNKNOWN":
            reasons.append("Condition could not be established")
        return reasons

    def run(self, job):
        try:
            history = self.history(job["property_id"])
            if history and history.get("blocked"):
                raise ValueError(history["block_reason"])
            lab = self.studio.prompts
            frozen = lab.result(job["id"])["manifest"]
            prop = frozen["properties"][0]
            photos = lab._frozen_photos(job["id"], prop)
            if not photos:
                raise ValueError("No usable photos")
            request = lab._request(frozen, "candidate", photos)
            schema = request["text"]["format"]["schema"]
            schema["properties"]["target_fit"] = {"type": "string", "enum": ["target", "not_target", "unsure"]}
            schema["properties"]["target_reason"] = {"type": "string"}
            schema["required"] += ["target_fit", "target_reason"]
            image_schema = schema["properties"]["images"]["items"]
            image_schema["properties"]["context"] = {"type": "string", "enum": ["subject", "shared_amenity", "floor_plan", "unknown"]}
            image_schema["properties"]["renovation_scope_score"] = {"type": ["number", "null"]}
            image_schema["required"] += ["context", "renovation_scope_score"]
            job["request_schema_sha256"] = hashlib.sha256(json.dumps(schema, sort_keys=True).encode()).hexdigest()
            job["calls_attempted"] = 1
            self.save(job)
            response = lab._call_model(request)
            raw, _ = lab._extract(response)
            reasons = self.validate(raw, prop["images"], lab)
            mapping = {p["image_id"]: p["source_image_id"] for p in prop["images"]}
            prediction = copy.deepcopy(raw)
            for image in prediction["images"]:
                image["citation_image_id"] = image["image_id"]
                image["image_id"] = mapping[image["image_id"]]
            job.update(status="completed", prediction=prediction,
                       proposed_target=raw["target_fit"],
                       review_target="unsure" if reasons else raw["target_fit"],
                       review_warnings=reasons, usage=lab._usage(response),
                       source="GPT draft + conservative coverage/context guard",
                       model=frozen["model"], prompt_hash=frozen["prompts"]["candidate"]["hash"],
                       final_sourcing_decision="NEEDS_REVIEW",
                       economics_status="Not connected; no comp values or costs invented",
                       completed_at=datetime.now(UTC).isoformat())
        except ValueError as error:
            job.update(status="failed", error=str(error)[:600])
        except Exception:
            job.update(status="failed", error="Assessment request failed; no automatic paid retry")
        self.save(job)

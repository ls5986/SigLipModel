"""Studio coordinator and same-origin local API dispatch; no automatic cloud work."""

import os
from urllib.parse import parse_qs, urlparse

from historical_store import HistoricalStore
from pilot import ROOT, read_json


def load_openai_key():
    value = os.environ.get("OPENAI_API_KEY")
    if value:
        return value
    raise ValueError("Set OPENAI_API_KEY in your environment or ignored .env file; no request was sent")


class Studio:
    def __init__(self, app):
        self.app = app
        self.store = HistoricalStore(app=app)
        self.store.seed()
        from prompt_lab import PromptLab

        self.prompts = PromptLab(
            ROOT, self.assessment_property, self.store.image_path,
            self.store.apply_proposals, load_openai_key,
        )
        from assessments import Assessments
        self.assessments = Assessments(self)
        from guarded_jobs import GuardedJobs
        self.jobs = GuardedJobs(self.store, self.assessments)
        from connected_worker import ConnectedWorker
        self.connection = ConnectedWorker(self.exchange())

    def exchange(self):
        if not hasattr(self, "_exchange"):
            from acq_exchange import Exchange
            self._exchange = Exchange(self.jobs)
        return self._exchange

    def assessment_property(self, identifier):
        history = self.assessments.history(identifier)
        if history and history.get("blocked"):
            raise ValueError(history["block_reason"])
        detail = self.store.property(identifier)
        detail["images"] = [image for image in detail["images"] if (
            (image.get("review") or {}).get("context") or image.get("provider_context")
        ) not in {"shared_amenity", "floor_plan", "unrelated"}]
        return detail

    def save_review(self, payload):
        if payload.get("label_schema_version"):
            from studio_v2 import label_evidence, validate_text_reviews
            detail = self.store.property(payload.get("id"))
            prop = detail["property"]
            if payload.get("label_evidence_id") != label_evidence(
                prop["id"], prop.get("mls_remarks") or "", detail["images"], prop.get("metadata")
            ):
                raise RuntimeError("Evidence changed; reload before saving labels")
            validate_text_reviews(payload.get("text_signals", []), prop.get("mls_remarks") or "")
        if payload.get("status") == "approved":
            identifier = payload.get("id")
            if payload.get("kind") == "image":
                with self.store.connect() as db:
                    row = db.execute("SELECT property_id FROM images WHERE id=?", (identifier,)).fetchone()
                identifier = row["property_id"] if row else None
            history = self.assessments.history(identifier) if identifier else None
            if history and history.get("blocked"):
                raise ValueError("This set is quarantined as wrong/ambiguous acquisition evidence. Save its photo-era judgment instead of condition/target labels.")
        return self.store.save_review(payload)

    def summary(self):
        self.app.reload_candidate()
        pointer = ROOT / "artifacts" / "studio_candidate_latest.json"
        return {
            **self.store.summary(), "token": self.app.token,
            "candidate": self.app.candidate,
            "studio_candidate": read_json(pointer) if pointer.exists() else None,
            "jobs": self.jobs.list_jobs(), "prompts": self.prompts.list_prompts(),
            "prompt_runs": self.prompts.list_runs(),
            "historical_cohort": self.assessments.history(),
        }

    def get(self, raw_path):
        parsed = urlparse(raw_path)
        args = {key: values[0] for key, values in parse_qs(parsed.query).items()}
        path = parsed.path
        if path.startswith("/api/studio/v2/"):
            from studio_v2 import get
            return get(self, raw_path)
        if path == "/api/studio/connection":
            return self.connection.public_status()
        if path == "/api/studio/model-loop":
            from model_loop import status
            return status(self.jobs)
        if path == "/api/studio/acq-comparison":
            return self.exchange().result(args.get("id"))
        if path == "/api/studio/review-queue":
            from review_queue import review_queue
            return review_queue(self, args)
        if path == "/api/studio/summary":
            return self.summary()
        if path == "/api/studio/properties":
            return self.store.properties(
                search=args.get("search", ""), queue=args.get("queue", "all"),
                offset=args.get("offset", 0), limit=args.get("limit", 24),
            )
        if path == "/api/studio/property":
            detail = self.store.property(args.get("id"))
            detail["assessment"] = self.assessments.latest(args.get("id"))
            detail["historical_source"] = self.assessments.history(args.get("id"))
            from photo_view import effective_photo
            ai_images = {i["image_id"]: i for i in (detail["assessment"] or {}).get("prediction", {}).get("images", [])} if (detail["assessment"] or {}).get("prediction") else {}
            for image in detail["images"]:
                image["effective"] = effective_photo(image, ai_images.get(image["id"]))
            return detail
        if path == "/api/studio/assessment":
            return self.assessments.job(args.get("id"))
        if path == "/api/studio/images":
            return self.store.image_list(
                room=args.get("room", ""), status=args.get("status", "all"),
                offset=args.get("offset", 0), limit=args.get("limit", 24),
                score_above=args.get("score_above"),
            )
        if path == "/api/studio/import/template":
            return self.store.import_template()
        if path == "/api/studio/prompt/result":
            return self.prompt_result(args.get("id"))
        raise ValueError("Unknown studio endpoint")

    def prompt_result(self, identifier):
        result = self.prompts.result(identifier)
        mappings = {
            prop["property_id"]: {image["image_id"]: image["source_image_id"]
                                  for image in prop["images"]}
            for prop in result["manifest"]["properties"]
        }
        records = []
        for prop in result["properties"]:
            for arm in ("baseline", "candidate"):
                outcome = prop[arm]
                raw = outcome.get("raw_output")
                prediction = None
                if isinstance(raw, dict):
                    prediction = {**raw, "images": [
                        {**image, "image_id": mappings[prop["property_id"]][image["image_id"]],
                         "citation_image_id": image["image_id"]}
                        for image in raw.get("images", []) if isinstance(image, dict)
                        and isinstance(image.get("image_id"), str)
                        and image["image_id"] in mappings[prop["property_id"]]
                    ]} if isinstance(raw.get("images", []), list) else {**raw, "images": []}
                error = outcome.get("error")
                validation = outcome.get("validation", {})
                if outcome["status"] == "validation_failed":
                    error = {"code": "output_validation_failed", "errors": validation.get("errors", [])}
                records.append({
                    "property_id": prop["property_id"], "arm": arm, "status": outcome["status"],
                    "prediction": prediction, "error": error, "usage": outcome.get("usage"),
                    "validation": validation,
                })
        return {
            **result, "results": records,
            "summary": {
                "calls_attempted": result.get("calls_attempted"), "usage": result.get("usage"),
                "comparison_note": result.get("comparison_note"),
                "caution": "Paired prompt outputs, not a best-prompt verdict or independent accuracy. "
                "Only accepted outputs may be applied, and only as drafts.",
            },
        }

    def post(self, path, payload):
        from studio_v2 import TRAINING_ACTIONS, require_operator
        if path in TRAINING_ACTIONS:
            require_operator()
        if path.startswith("/api/studio/v2/"):
            from studio_v2 import post
            return post(self, path, payload)
        if path == "/api/studio/connection":
            return self.connection.connect(payload)
        if path == "/api/studio/connection/disconnect":
            return self.connection.disconnect()
        if path == "/api/studio/acq-comparison/preview":
            return self.exchange().preview(payload)
        if path == "/api/studio/acq-comparison/run":
            return self.exchange().start(payload)
        if path in {"/api/studio/train", "/api/studio/prelabel"} and hasattr(self, "_exchange") and self._exchange.active and self._exchange.active.is_alive():
            raise ValueError("Wait for the local ACQ BOT comparison to finish first")
        if path == "/api/studio/assessment/preview":
            return self.assessments.preview(payload)
        if path == "/api/studio/assessment/run":
            return self.assessments.start(payload)
        if path == "/api/studio/era-review":
            return self.assessments.review_era(payload)
        actions = {
            "/api/studio/review": self.save_review,
            "/api/studio/import/commit": lambda data: self.store.commit_import(data.get("id")),
            "/api/studio/train/preview": lambda data: self.jobs.preview("train"),
            "/api/studio/train": lambda data: self.jobs.start(data, "train"),
            "/api/studio/prelabel/preview": lambda data: self.jobs.preview("prelabel"),
            "/api/studio/prelabel": lambda data: self.jobs.start(data, "prelabel"),
            "/api/studio/prompts": self.prompts.save_prompt,
            "/api/studio/prompt/preview": self.prompts.preview,
            "/api/studio/prompt/run": self.prompts.start,
            "/api/studio/prompt/apply": self.prompts.apply,
        }
        if path not in actions:
            raise ValueError("Unknown studio action")
        return actions[path](payload)

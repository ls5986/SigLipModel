"""Loopback-only UI for saved local model results and separate human annotations."""

from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import os
import secrets
import sqlite3
import tempfile
import threading
import webbrowser
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import unquote, urlparse

import joblib
import numpy as np

from config import CODE_ROOT
from pilot import ARTIFACTS, DATA, FEATURES, ROOT, SOURCE, read_json, write_json
from training_jobs import TrainingManager


class ReviewStore:
    def __init__(self, path: Path, allowed_images: set[str], allowed_properties: set[str],
                 rooms: list[str], allowed_room_images: set[str] | None = None,
                 image_splits: dict[str, str] | None = None, learning_mode: bool = False) -> None:
        self.path = path
        self.allowed_images = allowed_images
        self.allowed_properties = allowed_properties
        self.rooms = rooms
        self.allowed_room_images = (
            allowed_images if allowed_room_images is None else allowed_room_images
        )
        self.image_splits = image_splits or {key: "train" for key in self.allowed_room_images}
        self.learning_mode = learning_mode
        self.lock = threading.RLock()
        self.state = read_json(path) if path.exists() else {
            "revision": 0, "images": {}, "properties": {}, "history": [],
            "notice": "Human reviews are separate from machine labels and not automatically trained.",
        }

    def snapshot(self) -> dict:
        with self.lock:
            return json.loads(json.dumps(self.state))

    def save(self, payload: dict) -> dict:
        if not isinstance(payload, dict):
            raise ValueError("Expected a JSON object")
        kind = payload.get("kind")
        if kind not in {"image", "property", "room_preference", "room_correction",
                        "evaluation_preference"}:
            raise ValueError("Invalid review kind")
        specific = {
            "image": {"room", "features"},
            "property": {"target_fit", "reason"},
            "room_preference": {"room", "preference", "reason"},
            "evaluation_preference": {"room", "preference", "reason"},
            "room_correction": {"room", "reason"},
        }
        allowed = {"kind", "id", "expected_revision", "reviewer", "notes", "status"} | specific[kind]
        if set(payload) - allowed:
            raise ValueError("Unexpected review fields")
        identifier = payload.get("id")
        permitted = {
            "image": self.allowed_images, "property": self.allowed_properties,
            "room_preference": self.allowed_room_images,
            "room_correction": set(self.image_splits),
            "evaluation_preference": {key for key, split in self.image_splits.items()
                                      if split in {"validation", "test"}},
        }[kind]
        if identifier not in permitted:
            if kind in {"room_preference", "evaluation_preference", "room_correction"}:
                raise ValueError("Room preferences are training-only; validation/test stay protected")
            raise ValueError("Only this ten-property training review batch may be edited")
        reviewer = payload.get("reviewer")
        if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 100:
            raise ValueError("Enter a reviewer name or initials")
        notes = payload.get("notes", "")
        if not isinstance(notes, str) or len(notes) > 4000:
            raise ValueError("Notes must be at most 4000 characters")
        status = payload.get("status")
        if status not in {"draft", "approved"}:
            raise ValueError("Invalid review status")
        record = {"reviewer": reviewer.strip(), "notes": notes, "status": status,
                  "updated_at": datetime.now(UTC).isoformat(), "label_version": "human-v1"}
        if kind == "image":
            room = payload.get("room")
            if room not in [None, *self.rooms]:
                raise ValueError("Unsupported room")
            features = payload.get("features", {})
            if not isinstance(features, dict) or set(features) - set(FEATURES):
                raise ValueError("Unsupported feature names")
            if any(value is not None and (type(value) is not int or value not in (0, 1))
                   for value in features.values()):
                raise ValueError("Features must be present (1), absent (0), or unknown (null)")
            if status == "approved" and room is None:
                raise ValueError("Choose a room before approving image labels")
            record.update(room=room, features=features)
        elif kind == "property":
            target = payload.get("target_fit")
            reason = payload.get("reason", "")
            if target not in {None, "target", "not_target", "unsure"}:
                raise ValueError("Invalid target decision")
            if not isinstance(reason, str) or len(reason) > 2000:
                raise ValueError("Reason must be at most 2000 characters")
            if status == "approved" and (target is None or not reason.strip()):
                raise ValueError("Choose a target decision and enter a reason before approval")
            record.update(target_fit=target, reason=reason)
        elif kind == "room_correction":
            room = payload.get("room")
            if room not in self.rooms or status != "approved":
                raise ValueError("Choose the actual room, or Other / uncertain")
            reason = payload.get("reason", "")
            if not isinstance(reason, str) or len(reason) > 2000:
                raise ValueError("Invalid correction reason")
            record.update(
                room=room, reason=reason, split=self.image_splits[identifier],
                training_allowed=identifier in self.allowed_room_images,
                scope="Human room correction only; no feature or property preference inferred",
                prediction_visible_during_review=True,
            )
        else:
            room = payload.get("room")
            preference = payload.get("preference")
            reason = payload.get("reason", "")
            if room not in self.rooms:
                raise ValueError("Choose a supported room group")
            if preference not in {"target", "not_target", "unsure"}:
                raise ValueError("Choose Yes, No, or Cannot tell")
            if not isinstance(reason, str) or not 1 <= len(reason.strip()) <= 2000:
                raise ValueError("A short room-preference reason is required")
            record.update(
                room=room, preference=preference, reason=reason,
                scope="Visible renovation preference in this single photo, not property eligibility",
                room_label_source="grouping_only_unverified",
                property_target_inferred=False, feature_labels_inferred=False,
                split=self.image_splits[identifier],
                training_allowed=kind == "room_preference",
                prediction_visible_during_review=True,
            )
        with self.lock:
            if payload.get("expected_revision") != self.state["revision"]:
                raise RuntimeError("Reviews changed in another window. Reload before saving.")
            field = {"image": "images", "property": "properties",
                     "room_preference": "room_preferences", "room_correction": "room_corrections",
                     "evaluation_preference": "evaluation_preferences"}[kind]
            if kind in {"room_preference", "evaluation_preference"}:
                correction = self.state.get("room_corrections", {}).get(identifier, {})
                reviewed_image = self.state["images"].get(identifier, {})
                corrected_room = correction.get("room") if correction.get("status") == "approved" else (
                    reviewed_image.get("room") if reviewed_image.get("status") == "approved" else None
                )
                if corrected_room and corrected_room != room:
                    raise ValueError("Room changed; refresh photos before answering")
                if corrected_room == room:
                    record["room_label_source"] = "existing_human_approved_room"
            previous = self.state.get(field, {}).get(identifier)
            next_state = json.loads(json.dumps(self.state))
            next_state.setdefault(field, {})[identifier] = record
            next_state["revision"] += 1
            invalidations = []
            if kind == "room_correction":
                for preference_field in ("room_preferences", "evaluation_preferences"):
                    old_preference = next_state.get(preference_field, {}).get(identifier)
                    if old_preference and old_preference.get("room") != room and (
                        old_preference.get("status") == "approved"
                    ):
                        invalidations.append({"field": preference_field, "previous": old_preference.copy()})
                        old_preference["status"] = "needs_room_review"
                        old_preference["invalidated_by_room_correction_revision"] = next_state["revision"]
            next_state["history"].append({
                "kind": kind, "id": identifier, "previous": previous, "review": record,
                "revision": next_state["revision"],
                "invalidated_preferences": invalidations,
            })
            write_json(self.path, next_state)
            self.state = next_state
            return self.snapshot()


class AppData:
    def __init__(self) -> None:
        required = [
            DATA / "manifest.json", DATA / "embeddings.npz",
            DATA / "review_batch_10_properties.jsonl",
            ARTIFACTS / "metrics.json", ARTIFACTS / "silver_heads.joblib",
        ]
        missing = [str(path) for path in required if not path.is_file()]
        if missing:
            raise ValueError(
                "Private model/data artifacts are not installed. Set ACQ_DATA_ROOT "
                "to your existing pilot directory or restore its verified backup. "
                "Missing: " + ", ".join(missing)
            )
        self.manifest = read_json(DATA / "manifest.json")
        self.metrics = read_json(ARTIFACTS / "metrics.json")
        self.bundle = joblib.load(ARTIFACTS / "silver_heads.joblib")
        vectors = np.load(DATA / "embeddings.npz", allow_pickle=False)
        if vectors["dataset_sha256"].item() != self.manifest["dataset_sha256"]:
            raise ValueError("Embedding/manifest mismatch")
        if self.bundle["dataset_sha256"] != self.manifest["dataset_sha256"]:
            raise ValueError("Model/manifest mismatch")
        self.index = {value: i for i, value in enumerate(vectors["image_ids"].tolist())}
        self.rows = {row["image_id"]: row for row in self.manifest["images"]}
        self.embeddings = vectors["embeddings"]
        self.classes = self.bundle["room_model"].classes_.tolist()
        self.room_predictions = self.bundle["room_model"].predict(self.embeddings)
        self.room_scores = self.bundle["room_model"].predict_proba(self.embeddings)
        self.feature_scores = {
            feature: model.predict_proba(self.embeddings)[:, list(model.classes_).index(1)]
            for feature, model in self.bundle["feature_models"].items()
        }
        self.batch = [
            json.loads(line) for line in (DATA / "review_batch_10_properties.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()
        ]
        self.batch_by = {row["image_id"]: row for row in self.batch}
        self.candidate = None
        self.candidate_predictions = {}
        self.reload_candidate()
        self.store = ReviewStore(
            DATA / "human_reviews.json", set(self.batch_by),
            {row["listing_key"] for row in self.batch}, self.classes,
            {key for key, row in self.rows.items() if row["split"] != "test"},
            {key: row["split"] for key, row in self.rows.items()}, learning_mode=True,
        )
        self.training = TrainingManager(self.store)
        self.token = secrets.token_urlsafe(32)
        self.studio = None
        self.studio_lock = threading.RLock()

    def get_studio(self):
        with self.studio_lock:
            if self.studio is None:
                from studio_api import Studio

                self.studio = Studio(self)
            return self.studio

    def reload_candidate(self):
        candidate_path = ARTIFACTS / "candidate_latest.json"
        if candidate_path.exists():
            pointer = read_json(candidate_path)
            if self.candidate and self.candidate["version"] == pointer["version"]:
                return
            folder = Path(pointer["folder"]).resolve()
            if not folder.is_relative_to((ARTIFACTS / "candidates").resolve()):
                raise ValueError("Candidate artifact path is outside the pilot")
            candidate_metrics = read_json(folder / "metrics.json")
            if candidate_metrics["dataset_sha256"] != self.manifest["dataset_sha256"]:
                raise ValueError("Candidate dataset does not match current image manifest")
            self.candidate_predictions = read_json(folder / "predictions.json")
            self.candidate = {
                "version": pointer["version"], "review_revision": pointer["review_revision"],
                "room_corrections": candidate_metrics["changed_room_labels"],
                "room_approvals": candidate_metrics["room_approvals"],
                "preference_heads": {
                    room: {key: value for key, value in metrics.items()
                           if key not in {"labels", "folds", "out_of_fold_predictions"}}
                    for room, metrics in candidate_metrics["preference_heads"].items()
                },
                "skipped_rooms": candidate_metrics["skipped_rooms"],
                "limitations": candidate_metrics["limitations"],
                "production_ready": False,
                "learning_mode": pointer.get("learning_mode", False),
                "learning_partition": candidate_metrics.get("learning_partition"),
            }

    def record(self, identifier: str) -> dict:
        row = self.rows[identifier]
        i = self.index[identifier]
        return {
            "id": identifier, "listing_key": row["listing_key"], "split": row["split"],
            "learning_allowed": row["split"] != "test",
            "source_role": row["source_role"], "draft_room": row["room_label"],
            "siglip_room": str(self.room_predictions[i]),
            "room_scores": {name: float(value) for name, value in
                            zip(self.classes, self.room_scores[i])},
            "feature_scores": {name: float(values[i]) for name, values in self.feature_scores.items()},
            "draft_features": row["feature_labels"], "draft_observation": row["machine_observation"],
            "room_disagreement": self.room_predictions[i] != row["room_label"],
            "reviewable": identifier in self.batch_by,
            "address": self.batch_by.get(identifier, {}).get("address"),
            "image_order": row.get("image_order"),
            "candidate": self.candidate_predictions.get(identifier),
        }

    def bootstrap(self) -> dict:
        self.reload_candidate()
        return {
            "token": self.token, "rooms": self.classes, "features": list(FEATURES),
            "metrics": self.metrics, "dataset_summary": self.manifest["summary"],
            "images": [self.record(key) for key in self.rows],
            "review_batch": [self.record(row["image_id"]) for row in self.batch],
            "reviews": self.store.snapshot(),
            "candidate": self.candidate,
            "training": self.training.snapshot(),
            "learning_mode": True,
        }


def create_server(port: int, app: AppData) -> ThreadingHTTPServer:
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args) -> None:
            pass

        def authorized_host(self) -> bool:
            actual = self.server.server_address[1]
            return self.headers.get("Host") in {f"127.0.0.1:{actual}", f"localhost:{actual}"}

        def send(self, status: int, body: bytes, mime: str) -> None:
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Content-Security-Policy",
                             "default-src 'self'; script-src 'self' 'unsafe-inline'; "
                             "style-src 'self' 'unsafe-inline'; img-src 'self' blob:; "
                             "connect-src 'self'; object-src 'none'; frame-ancestors 'none'")
            self.end_headers()
            self.wfile.write(body)

        def json(self, status: int, payload: dict) -> None:
            self.send(status, json.dumps(payload).encode(), "application/json; charset=utf-8")

        def do_GET(self) -> None:
            if not self.authorized_host():
                self.json(403, {"error": "Loopback host required"})
                return
            path = urlparse(self.path).path
            if path == "/":
                page = (CODE_ROOT / "review_ui.html").read_bytes()
                if os.environ.get("STUDIO_DATA_BACKEND") == "supabase":
                    page = page.replace(b'/advanced?tab=experiments', b'/status#models').replace(b'/advanced?tab=dataset', b'/status#data')
                self.send(200, page, "text/html; charset=utf-8")
            elif path == "/source-rows" and os.environ.get("STUDIO_DATA_BACKEND") == "supabase":
                self.send(200, (CODE_ROOT / "source_rows.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/status" and os.environ.get("STUDIO_DATA_BACKEND") == "supabase":
                self.send(200, (CODE_ROOT / "hosted_status.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/workbench" and os.environ.get("STUDIO_DATA_BACKEND") == "supabase":
                self.send(200, (CODE_ROOT / "workbench_ui.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/connect":
                self.send(200, (CODE_ROOT / "connect_ui.html").read_bytes(), "text/html; charset=utf-8")
            elif path == "/legacy":
                self.send(200, (CODE_ROOT / "legacy_ui.html").read_bytes(), "text/html; charset=utf-8")
            elif path.startswith("/api/studio/"):
                try:
                    studio = app.get_studio()
                    if path == "/api/studio/workbench/challenge-image":
                        from urllib.parse import parse_qs

                        query = parse_qs(urlparse(self.path).query)
                        blob = studio.challenge_image(
                            query.get("listing_key", [""])[0],
                            query.get("media_key", [""])[0],
                            query.get("batch_id", [None])[0],
                        )
                        self.send(200, blob, "image/jpeg")
                    elif path == "/api/studio/image":
                        from urllib.parse import parse_qs

                        query = parse_qs(urlparse(self.path).query)
                        file = studio.store.image_path(query.get("id", [""])[0])
                        self.send(200, file.read_bytes(),
                                  mimetypes.guess_type(file.name)[0] or "image/jpeg")
                    else:
                        self.json(200, studio.get(self.path))
                except (ValueError, TypeError) as exc:
                    self.json(400, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    logging.exception("Studio read failed")
                    self.json(500, {"error": "Studio data unavailable. No local fallback was used."})
            elif os.environ.get("STUDIO_DATA_BACKEND") == "supabase" and path != "/api/health":
                self.json(400, {"error": "Legacy local tools are unavailable in cloud review mode"})
            elif path == "/api/bootstrap":
                self.json(200, app.bootstrap())
            elif path in {"/api/reviews", "/api/export"}:
                self.json(200, app.store.snapshot())
            elif path == "/api/health":
                self.json(200, {"status": "ready", "images": len(app.rows)})
            elif path == "/api/training":
                self.json(200, app.training.snapshot())
            elif path.startswith("/images/"):
                identifier = unquote(path.removeprefix("/images/"))
                row = app.rows.get(identifier)
                if row is None:
                    self.json(404, {"error": "Unknown image"})
                    return
                file = Path(row["image_path"]).resolve()
                if not file.is_relative_to((SOURCE / "media").resolve()) or not file.is_file():
                    self.json(404, {"error": "Evidence image unavailable"})
                    return
                self.send(200, file.read_bytes(), mimetypes.guess_type(file.name)[0] or "image/jpeg")
            else:
                self.json(404, {"error": "Not found"})

        def do_POST(self) -> None:
            actual = self.server.server_address[1]
            valid_origins = {f"http://127.0.0.1:{actual}", f"http://localhost:{actual}"}
            if not self.authorized_host() or self.headers.get("Origin") not in valid_origins or (
                not secrets.compare_digest(self.headers.get("X-Review-Token", ""), app.token)
            ):
                # Closing with unread small request bodies can reset the connection
                # on Windows before the browser receives the rejection.
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if 0 < length <= 32768:
                        self.connection.settimeout(1)
                        self.rfile.read(length)
                except (ValueError, OSError):
                    logging.warning("Rejected request body was incomplete or invalid")
                self.json(403, {"error": "Local review token and same origin required"})
                return
            if self.path.startswith("/api/studio/"):
                file = None
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    studio = app.get_studio()
                    if self.path.startswith("/api/studio/import/") and os.environ.get("STUDIO_DATA_BACKEND") == "supabase":
                        raise ValueError("Cloud imports require the separate verified import workflow")
                    if self.path == "/api/studio/import/preview":
                        if not 0 < length <= 1_000_000_000:
                            raise ValueError("ZIP must be between 1 byte and 1 GB")
                        self.connection.settimeout(180)
                        with tempfile.NamedTemporaryFile(
                            prefix="upload-", suffix=".zip", dir=studio.store.folder, delete=False,
                        ) as handle:
                            file = Path(handle.name)
                            remaining = length
                            while remaining:
                                chunk = self.rfile.read(min(1_000_000, remaining))
                                if not chunk:
                                    raise ValueError("Incomplete archive upload")
                                handle.write(chunk)
                                remaining -= len(chunk)
                        self.json(200, studio.store.preview_import(file))
                    else:
                        if not 0 < length <= 2_000_000:
                            raise ValueError("Invalid request size")
                        payload = json.loads(self.rfile.read(length))
                        if not isinstance(payload, dict):
                            raise ValueError("Expected JSON object")
                        self.json(200, studio.post(self.path, payload))
                except RuntimeError as exc:
                    self.json(409, {"error": str(exc)})
                except (ValueError, TypeError, KeyError) as exc:
                    self.json(400, {"error": str(exc)})
                except (OSError, sqlite3.Error):
                    logging.exception("Studio save or job failed")
                    self.json(500, {"error": "Local save or job could not complete; no success claimed"})
                finally:
                    if file and file.exists():
                        file.unlink()
                return
            if os.environ.get("STUDIO_DATA_BACKEND") == "supabase":
                self.json(400, {"error": "Legacy local writes are unavailable in cloud review mode"})
                return
            if self.path not in {"/api/reviews", "/api/train"}:
                self.json(404, {"error": "Not found"})
                return
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if not 0 < length <= 32768:
                    raise ValueError("Invalid request size")
                payload = json.loads(self.rfile.read(length))
                if self.path == "/api/train":
                    result = app.training.start(payload)
                    self.json(202 if result["status"] == "running" else 200, result)
                    return
                saved = app.store.save(payload)
                self.json(200, saved)
            except RuntimeError as exc:
                self.json(409, {"error": str(exc)})
            except (ValueError, TypeError) as exc:
                self.json(400, {"error": str(exc)})
            except OSError:
                self.json(500, {"error": "Could not save to disk; no success is being reported"})

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8768)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    try:
        backend = os.environ.get("STUDIO_DATA_BACKEND", "local")
        if backend == "supabase":
            from cloud_runtime import from_env
            app = from_env(allow_model_worker=True)
        elif backend == "local":
            app = AppData()
        else:
            raise ValueError("STUDIO_DATA_BACKEND must be local or supabase")
    except (ValueError, OSError) as error:
        parser.exit(2, f"Cannot start Studio: {error}\n")
    server = create_server(args.port, app)
    url = f"http://127.0.0.1:{server.server_address[1]}"
    write_json(ROOT / "ui_runtime.json", {"url": url, "started_at": str(datetime.now(UTC)),
                                          "scope": "local-only model results and annotation"})
    print(json.dumps({"url": url, "images": len(app.rows), "review_images": len(app.batch)}), flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

"""Development-only paired-condition inference and feedback service."""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Literal, Optional
from uuid import UUID, uuid5

from jsonschema import Draft202012Validator, FormatChecker

SCHEMA_VERSION = "actvision-paired-condition-v1"
RELEASE_ID = "f9bac37b-3ac4-4b64-9b54-cd9581bd226a"
CANDIDATE_SHA256 = "09a9cbe6877d9dfc6ee4899640c0b4dfd502d5be79a49f0d7be1cb1aa3556b6f"
MANIFEST_SHA256 = "3bcf16d679e032d8d7ac8520ca209ef6532c1b35472df8ca3ae5a29bedb2d273"
POLICY_VERSION = "remarks-condition-evidence-v3"
PACKAGE_KEY = "paired-dev-package:" + RELEASE_ID + ":" + POLICY_VERSION
Kind = Literal["release", "request", "prediction", "feedback", "receipt"]

SCHEMA = json.loads(
    (Path(__file__).parent / "contracts" / "paired-condition-v1.schema.json").read_text("utf-8")
)


def canonical_json(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _validate_semantics(payload: dict[str, Any], kind: Kind) -> None:
    if kind in {"request", "feedback"}:
        identity = payload["request_id"] if kind == "request" else payload["event_id"]
        if payload["idempotency_key"] != identity:
            raise ValueError("Idempotency identity mismatch")

    if kind == "request":
        seen: set[str] = set()
        for photo in payload["evidence"]["photos"]:
            if photo["photo_id"] in seen:
                raise ValueError("Duplicate photo identity")
            seen.add(photo["photo_id"])
            if hashlib.sha256(photo["uri"].encode("utf-8")).hexdigest() != photo["uri_sha256"]:
                raise ValueError("Photo URI fingerprint mismatch")
            if (photo["content_sha256"] is None) != (photo["content_base64"] is None):
                raise ValueError("Photo bytes and content fingerprint must be supplied together")

    if kind == "prediction":
        coverage = payload["coverage"]
        if (
            coverage["usable_photo_count"] + coverage["missing_photo_count"]
            != coverage["selected_photo_count"]
        ):
            raise ValueError("Photo coverage mismatch")
        if payload["status"] == "complete" and payload["modality_errors"]:
            raise ValueError("Complete predictions cannot contain modality errors")
        if payload["status"] == "partial" and not payload["modality_errors"]:
            raise ValueError("Partial predictions require explicit modality errors")
        if payload["modality_errors"] and payload["overall"]["decision"] == "NOT_TARGET":
            raise ValueError("Predictions with modality errors cannot be overall NOT_TARGET")
        if payload["status"] == "unavailable" and payload["overall"]["decision"] != (
            "INSUFFICIENT_EVIDENCE"
        ):
            raise ValueError("Unavailable predictions must remain insufficient evidence")


def validate_paired(payload: dict[str, Any], kind: Kind) -> dict[str, Any]:
    """Validate one contract payload without echoing private evidence in failures."""

    canonical_json(payload)
    validator = Draft202012Validator(
        {
            "$schema": SCHEMA["$schema"],
            "$defs": SCHEMA["$defs"],
            "$ref": f"#/$defs/{kind}",
        },
        format_checker=FormatChecker(),
    )
    error = next(validator.iter_errors(payload), None)
    if error:
        raise ValueError(f"Invalid paired-condition {kind} contract")
    _validate_semantics(payload, kind)
    return payload


def _read_verified(store: Any, bucket: str, key: str, expected_sha256: str) -> bytes:
    path = store.storage.get(bucket, key, expected_sha256)
    data = path.read_bytes()
    if hashlib.sha256(data).hexdigest() != expected_sha256:
        raise ValueError("Package artifact checksum mismatch")
    return data


def load_verified_package(store: Any) -> dict[str, Any]:
    """Load the immutable candidate only after every manifest artifact is verified."""

    registry = store.document(PACKAGE_KEY) or {}
    if (
        registry.get("status") != "ready"
        or registry.get("release_id") != RELEASE_ID
        or registry.get("manifest_sha256") != MANIFEST_SHA256
        or registry.get("candidate_sha256") != CANDIDATE_SHA256
        or registry.get("evidence_policy_version") != POLICY_VERSION
        or registry.get("production_ready") is not False
    ):
        raise ValueError("Exact paired development package is not ready")

    bucket = registry.get("storage_bucket", "acq-training-private")
    manifest_bytes = _read_verified(store, bucket, registry["manifest_key"], MANIFEST_SHA256)
    manifest = json.loads(manifest_bytes)
    if (
        manifest.get("schema_version") != "actvision-paired-dev-release-v1"
        or manifest.get("release_id") != RELEASE_ID
        or manifest.get("candidate_sha256") != CANDIDATE_SHA256
        or manifest.get("evidence_policy_version") != POLICY_VERSION
        or manifest.get("production_ready") is not False
    ):
        raise ValueError("Package manifest identity mismatch")

    artifacts = manifest.get("artifacts") or {}
    verified: dict[str, bytes] = {}
    for name, artifact in artifacts.items():
        verified[name] = _read_verified(
            store, manifest["storage_bucket"], artifact["object_key"], artifact["sha256"]
        )
    candidate_bytes = verified.get("candidate.json")
    if candidate_bytes is None or hashlib.sha256(candidate_bytes).hexdigest() != CANDIDATE_SHA256:
        raise ValueError("Candidate artifact identity mismatch")
    candidate = json.loads(candidate_bytes)
    if (
        candidate.get("candidate_id") != RELEASE_ID
        or candidate.get("schema_version") != SCHEMA_VERSION
    ):
        raise ValueError("Candidate payload identity mismatch")

    encoders = candidate.get("encoders") or manifest.get("encoders") or {}
    text_encoder = encoders.get("text") or {}
    release = {
        "schema_version": SCHEMA_VERSION,
        "release_id": RELEASE_ID,
        "candidate_sha256": CANDIDATE_SHA256,
        "manifest_sha256": MANIFEST_SHA256,
        "evidence_policy_version": POLICY_VERSION,
        "mode": "development",
        "production_ready": False,
        "encoders": {
            "vision_revision": encoders.get("vision_revision"),
            "text_checkpoint_sha256": text_encoder.get("checkpoint_sha256"),
        },
        "strength_meaning": "uncalibrated target strength, not probability or opportunity",
    }
    validate_paired(release, "release")
    if not isinstance(candidate.get("heads"), dict):
        raise ValueError("Candidate model heads are missing")
    return {"release": release, "bundle": candidate, "manifest": manifest}


def _unknown(reason: Optional[str] = None) -> dict[str, Any]:
    value = {"decision": "INSUFFICIENT_EVIDENCE", "strength": None, "affinity": None}
    if reason:
        value["reason"] = reason
    return value


class PairedConditionService:
    def __init__(
        self,
        store: Any,
        *,
        vision_encoder: Optional[Callable[[bytes, str], list[float]]] = None,
        text_encoder: Optional[Callable[[str, str], list[float]]] = None,
    ):
        self.store = store
        self._vision_encoder = vision_encoder or self._default_vision_encoder
        self._text_encoder = text_encoder or self._default_text_encoder
        self._vision_runtime: Any = None
        self._text_runtime: Any = None

    def release(self, release_id: str) -> dict[str, Any]:
        if release_id != RELEASE_ID:
            raise LookupError("Release not found")
        return load_verified_package(self.store)["release"]

    def _default_vision_encoder(self, content: bytes, identity: str) -> list[float]:
        from automatic_labels import SiglipLabels
        from PIL import Image, ImageOps

        if self._vision_runtime is None:
            self._vision_runtime = SiglipLabels()
            if self._vision_runtime.revision != identity:
                raise ValueError("Vision encoder differs from package manifest")
            self._vision_runtime.torch.set_num_threads(1)
        with Image.open(io.BytesIO(content)) as source:
            image = ImageOps.exif_transpose(source).convert("RGB")
        inputs = self._vision_runtime.processor(images=[image], return_tensors="pt")
        with self._vision_runtime.torch.inference_mode():
            vectors = self._vision_runtime.model.get_image_features(
                pixel_values=inputs["pixel_values"]
            ).float()
            vectors = self._vision_runtime.torch.nn.functional.normalize(
                vectors, dim=-1
            )
        return vectors.cpu().tolist()[0]

    def _default_text_encoder(self, text: str, identity: str) -> list[float]:
        if self._text_runtime is None:
            from provision_semantic_encoder import DIRECTORY, verify
            from semantic_text import SemanticTextEncoder

            provisioned = verify()
            if provisioned["checkpoint_sha256"] != identity:
                raise ValueError("Text encoder differs from package manifest")
            self._text_runtime = SemanticTextEncoder(DIRECTORY, identity)
        return self._text_runtime.transform([text]).toarray()[0].tolist()

    def _photo_features(
        self, photos: list[dict[str, Any]], encoder_identity: str
    ) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
        model_photos = []
        evidence = []
        errors = []
        for photo in photos:
            result = _unknown()
            error = None
            embedding = None
            if photo["exclusion"] == "none":
                if photo["content_base64"] is None:
                    error = "Selected photo bytes were unavailable."
                else:
                    try:
                        content = base64.b64decode(photo["content_base64"], validate=True)
                    except (binascii.Error, ValueError) as exc:
                        raise ValueError("Invalid photo byte encoding") from exc
                    if hashlib.sha256(content).hexdigest() != photo["content_sha256"]:
                        raise ValueError("Photo content fingerprint mismatch")
                    cache_identity = {
                        "content_sha256": photo["content_sha256"],
                        "encoder": encoder_identity,
                    }
                    cache_key = "paired-condition-embedding:" + digest(cache_identity)
                    cached = self.store.document(cache_key) or {}
                    if cached:
                        if cached.get("identity") != cache_identity:
                            raise ValueError("Cached embedding identity mismatch")
                        embedding = cached.get("embedding")
                        if not isinstance(embedding, list) or not embedding:
                            raise ValueError("Cached embedding value is incomplete")
                    if embedding is None:
                        try:
                            embedding = self._vision_encoder(content, encoder_identity)
                        except Exception:
                            error = "Image encoder failed."
                            errors.append(
                                {
                                    "modality": "image",
                                    "code": "image_encoder_failed",
                                    "message": error,
                                    "retryable": True,
                                }
                            )
                            embedding = None
                        if not isinstance(embedding, list) or not embedding:
                            if error is None:
                                error = "Image encoder failed."
                                errors.append(
                                    {
                                        "modality": "image",
                                        "code": "image_encoder_failed",
                                        "message": error,
                                        "retryable": True,
                                    }
                                )
                        else:
                            cache_value = {
                                "identity": cache_identity,
                                "embedding": embedding,
                            }
                            try:
                                self.store.save_document(
                                    cache_key,
                                    cache_value,
                                    cached.get("revision", 0),
                                )
                            except (RuntimeError, ValueError):
                                concurrent = self.store.document(cache_key) or {}
                                if (
                                    concurrent.get("identity") != cache_identity
                                    or concurrent.get("embedding") != embedding
                                ):
                                    raise ValueError(
                                        "Concurrent cached embedding mismatch"
                                    ) from None
                                embedding = concurrent["embedding"]
                    if embedding is not None:
                        model_photos.append(
                            {
                                "photo_id": photo["photo_id"],
                                "sha256": photo["content_sha256"],
                                "room": photo["room"],
                                "exclusion": "none",
                                "image_embedding": embedding,
                            }
                        )
            evidence.append(
                {
                    "photo_id": photo["photo_id"],
                    "provider_media_id": photo["provider_media_id"],
                    "content_sha256": photo["content_sha256"],
                    "room": photo["room"],
                    "exclusion": photo["exclusion"],
                    "result": result,
                    "error": error,
                }
            )
            if error:
                if error == "Selected photo bytes were unavailable.":
                    errors.append(
                        {
                            "modality": "image",
                            "code": "missing_photo_bytes",
                            "message": error,
                            "retryable": True,
                        }
                    )
        return model_photos, evidence, errors

    def infer(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_paired(payload, "request")
        package = load_verified_package(self.store)
        if (
            payload["release_id"] != package["release"]["release_id"]
            or payload["manifest_sha256"] != package["release"]["manifest_sha256"]
        ):
            raise ValueError("Requested package identity mismatch")

        request_digest = digest(payload)
        result_key = "paired-condition-prediction:" + payload["request_id"]
        previous = self.store.document(result_key) or {}
        if previous:
            if previous.get("request_sha256") != request_digest:
                raise RuntimeError("Request ID already exists with different content")
            return validate_paired(previous["prediction"], "prediction")

        from paired_condition_evidence import apply_policy
        from release_sources.trained_classifier import (
            classify,
            has_input,
            predict,
            predict_component,
            sanitize_text,
            vision_features,
        )

        evidence = payload["evidence"]
        cleaned = sanitize_text(evidence["public_remarks"], evidence["private_remarks"])
        release = package["release"]
        model_photos, photo_evidence, errors = self._photo_features(
            evidence["photos"], release["encoders"]["vision_revision"]
        )
        text_vector = None
        if cleaned:
            try:
                text_vector = self._text_encoder(
                    cleaned, release["encoders"]["text_checkpoint_sha256"]
                )
            except Exception:
                errors.append(
                    {
                        "modality": "remarks",
                        "code": "text_encoder_failed",
                        "message": "Text encoder failed.",
                        "retryable": True,
                    }
                )
        row = {
            "photos": model_photos,
            "structured": evidence["structured"],
            "remarks": cleaned,
            "vision_vector": vision_features(model_photos),
            "text_vector": text_vector,
        }
        photo_rows = [
            {
                "photos": [photo],
                "vision_vector": vision_features([photo]),
            }
            for photo in model_photos
        ]
        classifier_failed = False
        try:
            photo_scores = (
                predict_component(
                    "vision", package["bundle"]["heads"].get("vision"), photo_rows
                )
                if photo_rows
                else []
            )
            photo_results = {
                photo["photo_id"]: classify(score)
                for photo, score in zip(model_photos, photo_scores)
            }
            for photo in photo_evidence:
                if photo["photo_id"] in photo_results:
                    photo["result"] = photo_results[photo["photo_id"]]
            raw = predict(package["bundle"], [row])[0]
        except Exception:
            classifier_failed = True
            raw = {
                "image": _unknown(),
                "text": _unknown(),
                "structured": _unknown(),
                "metadata": _unknown(),
                "overall": _unknown(),
            }
            errors.append(
                {
                    "modality": "overall",
                    "code": "classifier_failed",
                    "message": "Condition classifier failed.",
                    "retryable": True,
                }
            )
        learned = {
            "image": raw["image"],
            "remarks": raw["text"],
            "structured": raw["structured"],
            "metadata": raw["metadata"],
            "overall": raw["overall"],
        }
        policy_input = {
            "status": "completed",
            "image": raw["image"],
            "text": raw["text"],
            "structured": raw["structured"],
            "metadata": raw["metadata"],
            "overall": raw["overall"],
            "usable_photo_count": len(model_photos),
        }
        adjusted = apply_policy(
            policy_input, evidence["public_remarks"], evidence["private_remarks"] or ""
        )
        if classifier_failed:
            for component in ("image", "text", "structured", "metadata", "overall"):
                adjusted[component] = _unknown()

        availability = {
            "image": has_input(row, "vision"),
            "remarks": has_input(row, "text"),
            "structured": has_input(row, "structured"),
        }
        if (
            not classifier_failed
            and not availability["image"]
            and not any(e["modality"] == "image" for e in errors)
        ):
            errors.append(
                {
                    "modality": "image",
                    "code": "missing_image_evidence",
                    "message": "No usable condition photo evidence was available.",
                    "retryable": False,
                }
            )
        for contract_name, model_name, message in (
            ("remarks", "text", "No meaningful sanitized remarks were available."),
            ("structured", "structured", "No usable condition-model fields were available."),
        ):
            if (
                not classifier_failed
                and not availability[contract_name]
                and not any(e["modality"] == contract_name for e in errors)
            ):
                errors.append(
                    {
                        "modality": contract_name,
                        "code": "missing_" + contract_name + "_evidence",
                        "message": message,
                        "retryable": False,
                    }
                )
        if errors and adjusted["overall"]["decision"] == "NOT_TARGET":
            adjusted["overall"] = _unknown(
                "Missing modality evidence prevents a safe NOT TARGET decision."
            )
            adjusted["evidence_policy"]["reason"] = "missing_modality_evidence"
            adjusted["evidence_policy"]["needs_review"] = True
        if classifier_failed:
            status = "unavailable"
        elif all(availability.values()) and not errors:
            status = "complete"
        else:
            status = "partial" if any(availability.values()) else "unavailable"
        prediction_id = str(
            uuid5(UUID("d2263ec8-77cd-4f13-8f14-c3235bbc57df"), payload["request_id"])
        )
        prediction = {
            "schema_version": SCHEMA_VERSION,
            "prediction_id": prediction_id,
            "request_id": payload["request_id"],
            "evidence_id": payload["evidence_id"],
            "release": release,
            "status": status,
            "image": adjusted["image"],
            "remarks": adjusted["text"],
            "structured": adjusted["structured"],
            "metadata": adjusted["metadata"],
            "overall": adjusted["overall"],
            "learned_prediction": learned,
            "evidence_policy": adjusted["evidence_policy"],
            "photo_evidence": photo_evidence,
            "coverage": {
                "selected_photo_count": len(evidence["photos"]),
                "usable_photo_count": len(model_photos),
                "missing_photo_count": len(evidence["photos"]) - len(model_photos),
            },
            "modality_errors": errors,
            "created_at": datetime.now(timezone.utc).isoformat(),
        }
        validate_paired(prediction, "prediction")
        stored = {
            "request_sha256": request_digest,
            "prediction": prediction,
            "status": "completed",
        }
        try:
            self.store.save_document(result_key, stored, 0)
        except (RuntimeError, ValueError):
            concurrent = self.store.document(result_key) or {}
            if concurrent.get("request_sha256") != request_digest:
                raise RuntimeError(
                    "Request ID already exists with different content"
                ) from None
            return validate_paired(concurrent["prediction"], "prediction")
        return prediction

    def receive_feedback(self, payload: dict[str, Any]) -> dict[str, Any]:
        validate_paired(payload, "feedback")
        package = load_verified_package(self.store)
        if payload["release"] != package["release"]:
            raise ValueError("Feedback release identity mismatch")
        stored = self.store.document(
            "paired-condition-prediction:" + payload["request_id"]
        ) or {}
        prediction = stored.get("prediction")
        scored_input = payload["scored_input"]
        request = scored_input.get("request")
        expected_display = (
            {
                key: prediction[key]
                for key in ("image", "remarks", "structured", "metadata", "overall")
            }
            if isinstance(prediction, dict)
            else None
        )
        if (
            stored.get("status") != "completed"
            or not isinstance(prediction, dict)
            or not isinstance(request, dict)
            or stored.get("request_sha256") != digest(request)
            or prediction.get("prediction_id") != payload["prediction_id"]
            or prediction.get("request_id") != payload["request_id"]
            or prediction.get("evidence_id") != payload["evidence_id"]
            or prediction.get("release") != payload["release"]
            or scored_input.get("learned_prediction")
            != prediction.get("learned_prediction")
            or scored_input.get("displayed_decisions") != expected_display
            or scored_input.get("evidence_policy") != prediction.get("evidence_policy")
            or scored_input.get("coverage") != prediction.get("coverage")
            or scored_input.get("modality_errors") != prediction.get("modality_errors")
        ):
            raise ValueError("Feedback prediction provenance mismatch")
        event_digest = digest(payload)
        key = "paired-condition-feedback-inbox:" + payload["event_id"]
        existing = self.store.document(key) or {}
        if existing:
            if existing.get("event_sha256") != event_digest or existing.get("event") != payload:
                raise RuntimeError("Feedback event ID already exists with different content")
            receipt = {
                "schema_version": SCHEMA_VERSION,
                "event_id": payload["event_id"],
                "status": "duplicate",
                "inbox_status": "pending",
            }
            return validate_paired(receipt, "receipt")
        inbox = {
            "status": "pending",
            "event_sha256": event_digest,
            "event": payload,
            "received_at": datetime.now(timezone.utc).isoformat(),
            "training_approved": False,
        }
        try:
            self.store.save_document(key, inbox, 0)
        except (RuntimeError, ValueError):
            concurrent = self.store.document(key) or {}
            if concurrent.get("event_sha256") != event_digest or concurrent.get("event") != payload:
                raise RuntimeError(
                    "Feedback event ID already exists with different content"
                ) from None
            receipt_status = "duplicate"
        else:
            receipt_status = "accepted"
        receipt = {
            "schema_version": SCHEMA_VERSION,
            "event_id": payload["event_id"],
            "status": receipt_status,
            "inbox_status": "pending",
        }
        return validate_paired(receipt, "receipt")


def approved_release(store: Any, release_id: str) -> dict[str, Any]:
    return PairedConditionService(store).release(release_id)


def infer(store: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return PairedConditionService(store).infer(payload)


def receive_feedback(store: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return PairedConditionService(store).receive_feedback(payload)

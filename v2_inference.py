"""Durable authenticated ActVision v2 inference jobs for the hosted model lane."""
from __future__ import annotations

import base64
import binascii
import hashlib
import io
import os
import tempfile
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse
from uuid import uuid4

import httpx
from PIL import Image

from actvision_contract import digest, validate_contract
from studio_data import now
from v2_runtime import predict_local, release_manifest

REQUEST_PREFIX = "actvision-inference-request:"
RESULT_PREFIX = "actvision-inference-result:"
_token = None
_token_expires = datetime.min.replace(tzinfo=UTC)


def _trusted_photo_url(value):
    if not isinstance(value, str) or len(value) > 4000:
        return False
    parsed = urlparse(value)
    return (
        parsed.scheme == "https"
        and parsed.hostname == "api.cotality.com"
        and parsed.username is None
        and parsed.password is None
        and parsed.port in {None, 443}
    )


def _access_token(client):
    global _token, _token_expires
    if _token and datetime.now(UTC) < _token_expires:
        return _token
    client_id = os.environ.get("TRESTLE_CLIENT_ID", "")
    secret = os.environ.get("TRESTLE_CLIENT_SECRET", "")
    if not client_id or not secret:
        raise RuntimeError("Cotality credentials are not configured on the ActVision worker")
    response = client.post(
        "https://api.cotality.com/trestle/oidc/connect/token",
        data={
            "client_id": client_id, "client_secret": secret,
            "grant_type": "client_credentials", "scope": "api",
        },
        headers={"Accept": "application/json"},
    )
    response.raise_for_status()
    payload = response.json()
    token = payload.get("access_token")
    expires = payload.get("expires_in", 3600)
    if not isinstance(token, str) or not token:
        raise RuntimeError("Cotality authentication omitted an access token")
    _token = token
    _token_expires = datetime.now(UTC) + timedelta(seconds=max(60, int(expires) - 300))
    return token


def _download(payload, directory):
    photos = []
    observed = []
    warnings = []
    if not payload["photo_inputs"]:
        return photos, observed, warnings
    with httpx.Client(timeout=30, follow_redirects=False, trust_env=False) as client:
        token = None
        for index, item in enumerate(payload["photo_inputs"]):
            uri = item["uri"]
            if not _trusted_photo_url(uri):
                raise ValueError("ActVision photo origin is not trusted")
            inline = item.get("content_b64")
            if inline is not None:
                try:
                    blob = base64.b64decode(inline, validate=True)
                except (binascii.Error, ValueError) as exc:
                    raise ValueError("ActVision inline photo encoding is invalid") from exc
                if not 0 < len(blob) <= 160_000:
                    raise ValueError("ActVision inline photo exceeded the transport limit")
                if hashlib.sha256(blob).hexdigest() != item.get("content_sha256"):
                    raise ValueError("ActVision inline photo hash mismatch")
            else:
                # Backward-compatible fallback for older MLS shadow requests.
                # New requests hydrate bounded image bytes in the MLS worker.
                response = client.get(uri, headers={"Accept": "image/*"})
                if response.status_code == 401:
                    global _token
                    _token = None
                    token = _access_token(client)
                    response = client.get(
                        uri, headers={"Authorization": "Bearer " + token, "Accept": "image/*"}
                    )
                if response.status_code != 200:
                    warnings.append(f"Photo {item['photo_id']} could not be retrieved")
                    continue
                blob = response.content
                if not 0 < len(blob) <= 8_000_000:
                    warnings.append(f"Photo {item['photo_id']} exceeded the bounded image size")
                    continue
            try:
                with Image.open(io.BytesIO(blob)) as image:
                    if image.format not in {"JPEG", "PNG", "WEBP"} or image.width * image.height > 40_000_000:
                        raise ValueError
                    image.verify()
            except Exception:
                warnings.append(f"Photo {item['photo_id']} was not a supported image")
                continue
            sha = hashlib.sha256(blob).hexdigest()
            expected = next(
                photo["sha256"] for photo in payload["evidence"]["selected_photos"]
                if photo["photo_id"] == item["photo_id"]
            )
            if expected is not None and expected != sha:
                raise ValueError("Observed image content hash differs from requested evidence")
            path = Path(directory) / f"{index}-{sha}.img"
            path.write_bytes(blob)
            photos.append({
                "path": path, "photo_id": item["photo_id"], "sha256": sha, "room": "other",
            })
            observed.append({"photo_id": item["photo_id"], "sha256": sha})
    return photos, observed, warnings


def process(store, request, siglip):
    key = REQUEST_PREFIX + request["request_id"]
    if request.get("status") != "queued":
        return False
    active = store.save_document(
        key, {**request, "status": "running", "started_at": now()}, request["revision"]
    )
    started = time.monotonic()
    try:
        payload = active["payload"]
        validate_contract(payload, "inference_request")
        release_manifest(store, payload["release_id"] if "release_id" in payload else payload["evidence"]["release_id"],
                         allowed_statuses=("shadow", "production"))
        with tempfile.TemporaryDirectory(prefix="actvision-infer-") as directory:
            photos, observed, warnings = _download(payload, directory)
            usable_photos = []
            if photos:
                room_drafts = siglip.classify([photo["path"] for photo in photos])
                for photo, room in zip(photos, room_drafts):
                    photo["room"] = room.get("room") or "other"
                    if room.get("context") == "subject":
                        usable_photos.append(photo)
                    else:
                        warnings.append(
                            f"Photo {photo['photo_id']} excluded as non-subject or uncertain context"
                        )
            predicted = predict_local(
                store, siglip, payload["evidence"]["release_id"],
                remarks=payload["public_remarks"], structured=payload["structured"],
                photos=usable_photos, selected_photo_count=len(payload["photo_inputs"]),
                allowed_statuses=("shadow", "production"),
            )
        prediction = {
            "kind": "prediction", "schema_version": "actvision-v2",
            "label_schema_version": "actvision-labels-v2",
            "prediction_id": str(uuid4()), "request_id": payload["request_id"],
            "evidence_id": payload["evidence_id"], "evidence": payload["evidence"],
            "release_id": payload["evidence"]["release_id"],
            "status": predicted["status"], "components": predicted["components"],
            "result": predicted["result"], "modalities_used": predicted["modalities_used"],
            "coverage": predicted["coverage"], "observed_photo_hashes": observed,
            "created_at": now(), "latency_ms": int((time.monotonic() - started) * 1000),
            "warnings": warnings,
        }
        validate_contract(prediction, "prediction")
        result_key = RESULT_PREFIX + payload["request_id"]
        previous = store.document(result_key) or {}
        store.save_document(result_key, {
            "status": "completed", "request_sha256": active["request_sha256"],
            "prediction": prediction, "at": now(),
        }, previous.get("revision", 0))
        store.save_document(
            key, {**active, "status": "completed", "completed_at": now()}, active["revision"]
        )
        return True
    except Exception as exc:
        current = store.document(key) or active
        if current.get("status") == "running":
            store.save_document(
                key, {**{k: v for k, v in current.items() if k != "revision"},
                      "status": "failed", "failed_at": now(),
                      "error_code": type(exc).__name__,
                      "error": "ActVision inference failed; no prediction was fabricated."},
                current["revision"],
            )
        return False


def enqueue(store, payload):
    validate_contract(payload, "inference_request")
    release_manifest(store, payload["evidence"]["release_id"], allowed_statuses=("shadow", "production"))
    identity = digest(payload)
    result = store.document(RESULT_PREFIX + payload["request_id"])
    if result:
        if result.get("request_sha256") != identity:
            raise RuntimeError("Inference request ID was reused with different evidence")
        if result.get("status") == "completed":
            return result["prediction"]
    key = REQUEST_PREFIX + payload["request_id"]
    previous = store.document(key) or {}
    if previous:
        if previous.get("request_sha256") != identity:
            raise RuntimeError("Inference request ID was reused with different evidence")
        return None
    store.save_document(key, {
        "status": "queued", "request_id": payload["request_id"],
        "request_sha256": identity, "payload": payload, "queued_at": now(),
    }, 0)
    return None


def poll(store, siglip):
    with store.database.connect() as db:
        row = db.execute("""
          SELECT payload,revision FROM acq_training.studio_state
          WHERE workspace_id=%s AND kind='document'
            AND item_id LIKE 'actvision-inference-request:%%'
            AND payload->>'status'='queued'
          ORDER BY payload->>'queued_at' LIMIT 1
        """, (store.workspace,)).fetchone()
    if not row:
        return False
    return process(store, {**row["payload"], "revision": row["revision"]}, siglip)

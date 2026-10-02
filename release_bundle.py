"""Validate trusted operator-provisioned release bytes without deserializing code."""
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from actvision_contract import validate_contract

FEATURE_SCHEMAS = {
    "vision": {"actvision-siglip2-heads-v2"},
    "text": {"actvision-text-tfidf-v2", "actvision-text-encoder-v2"},
    "structured": {"actvision-structured-v2"},
    "fusion": {"actvision-fusion-v2"},
}


@dataclass(frozen=True)
class ReleaseBundle:
    manifest: dict
    artifacts: dict
    manifest_sha256: str


def load_release_bundle(manifest_path, *, expected_release_id=None, allowed_statuses=("shadow", "production"),
                        frameworks=None):
    path = Path(manifest_path).resolve(strict=True)
    raw = path.read_bytes()
    manifest = validate_contract(json.loads(raw), "release")
    if expected_release_id and manifest["release_id"] != expected_release_id:
        raise ValueError("Configured release does not match requested release")
    if manifest["status"] not in allowed_statuses:
        raise ValueError("Release is not approved for the requested use")
    # The caller supplies installed adapter frameworks/versions, not versions taken from the bundle.
    frameworks = frameworks or {}
    artifacts = {}
    for name, artifact in manifest["components"].items():
        if artifact is None:
            continue
        if artifact["feature_schema_version"] not in FEATURE_SCHEMAS[name]:
            raise ValueError(f"Incompatible {name} feature schema")
        if frameworks.get(artifact["framework"]) != artifact["framework_version"]:
            raise ValueError(f"Unsupported {name} framework/version")
        uri = artifact["uri"]
        relative = Path(uri)
        if ":" in uri or relative.is_absolute() or ".." in relative.parts:
            raise ValueError("Artifact URI must be a local relative path within the trusted bundle")
        file = (path.parent / relative).resolve(strict=True)
        if not file.is_relative_to(path.parent):
            raise ValueError("Artifact escapes release directory")
        blob = file.read_bytes()
        if hashlib.sha256(blob).hexdigest() != artifact["sha256"]:
            raise ValueError(f"{name} artifact SHA256 mismatch")
        artifacts[name] = blob
    return ReleaseBundle(manifest, artifacts, hashlib.sha256(raw).hexdigest())

"""Offline, fail-closed verification for the existing ActVision v2 candidate."""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
from importlib.metadata import PackageNotFoundError, version as package_version
import json
import os
from pathlib import Path
import re
import resource
import sys
import time

from actvision_contract import canonical_json, validate_contract
from v2_calibration import CalibratedModel


@dataclass(frozen=True)
class ComponentExpectation:
    sha256: str
    feature_schema_version: str
    calibration_version: str


@dataclass(frozen=True)
class SmokeExpectations:
    release_id: str
    bundle_sha256: str
    components: dict[str, ComponentExpectation]
    framework_version: str
    vision_model: str
    vision_revision: str | None
    text_model: str
    text_revision: str
    text_checkpoint_sha256: str


@dataclass(frozen=True)
class SmokePaths:
    manifest: Path
    artifact_root: Path
    vision_provenance: Path
    text_checkpoint: Path


@dataclass(frozen=True)
class VerifiedSmokeInputs:
    manifest: dict
    artifact_paths: dict[str, Path]
    vision_snapshot: Path
    vision_revision: str
    text_checkpoint: Path


DEFAULT_EXPECTATIONS = SmokeExpectations(
    release_id="f134590b-122b-42dc-9a27-209523e2a33c",
    bundle_sha256="7527a12908fc62b6a94f86d3337ff5a8ccd25c6d7c50e291a016f5bf4d8dee00",
    components={
        "vision": ComponentExpectation(
            "95e4f26874850ebd65a90ab17b2731c9f5ec982086e67bf3262eff0864d92c01",
            "actvision-siglip2-heads-v2",
            "temperature-v1:3101ee63de0ef4e0",
        ),
        "text": ComponentExpectation(
            "79224f3d1037a2df286f14fcc80d40dc7f2a53b77e0b317518c207cf46dc933a",
            "actvision-text-semantic-v1",
            "temperature-v1:74f9fe626b22ef2e",
        ),
        "structured": ComponentExpectation(
            "21fcac42135029d81941ea05fa12de395ebce9cf1ecc0407472fee31318fa85c",
            "actvision-structured-v2",
            "temperature-v1:6512e85893a6133c",
        ),
        "fusion": ComponentExpectation(
            "fa87a668c06b4e3251f3499d3ac9a4a72a080cba9f9aa5cb9c86dc09472d0ae5",
            "actvision-fusion-v2",
            "temperature-v1:6df43267c0177965",
        ),
    },
    framework_version="1.6.1",
    vision_model="google/siglip2-base-patch16-224",
    vision_revision=None,
    text_model="sentence-transformers/all-MiniLM-L6-v2",
    text_revision="1110a243fdf4706b3f48f1d95db1a4f5529b4d41",
    text_checkpoint_sha256="dfdac870e42d2700ab4813bb3ad1dab8c9f992d4a99d5f50e4f5732610d30d92",
)

EXPECTED_RUNTIME = {
    "python": "3.12.14",
    "scikit-learn": "1.6.1",
    "joblib": "1.4.2",
    "torch": "2.6.0",
    "transformers": "4.57.6",
    "sentence-transformers": "5.1.2",
}


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _safe_child(root: Path, relative: str) -> Path:
    if ":" in relative or "\\" in relative or ".." in relative.split("/"):
        raise ValueError("Artifact URI is outside the local bundle")
    path = (root / relative).resolve(strict=True)
    root = root.resolve(strict=True)
    if not path.is_relative_to(root):
        raise ValueError("Artifact URI is outside the local bundle")
    return path


def _verify_vision(paths: SmokePaths, expected: SmokeExpectations) -> tuple[Path, str]:
    if expected.vision_revision is None:
        raise ValueError(
            "The exact original SigLIP2 revision is unverified; recover original "
            "worker provenance before running the load smoke"
        )
    if not re.fullmatch(r"[0-9a-f]{40}", expected.vision_revision):
        raise ValueError("Expected Vision encoder revision must be a full commit SHA")
    record = json.loads(paths.vision_provenance.read_text())
    if record.get("model_id") != expected.vision_model:
        raise ValueError("Vision encoder identity mismatch")
    if record.get("revision") != expected.vision_revision:
        raise ValueError("Vision encoder revision mismatch")
    snapshot = Path(record.get("snapshot_path", "")).resolve(strict=True)
    if snapshot.name != expected.vision_revision:
        raise ValueError("Vision snapshot path is not bound to the expected revision")
    weights = record.get("weights")
    if not isinstance(weights, dict) or not weights:
        raise ValueError("Vision provenance does not record checkpoint weights")
    for relative, identity in weights.items():
        file = _safe_child(snapshot, relative)
        if file.stat().st_size != identity.get("bytes"):
            raise ValueError(f"Vision checkpoint size mismatch for {relative}")
        if _sha256_file(file) != identity.get("sha256"):
            raise ValueError(f"Vision checkpoint SHA256 mismatch for {relative}")
    return snapshot, expected.vision_revision


def verify_inputs(
    paths: SmokePaths,
    expected: SmokeExpectations = DEFAULT_EXPECTATIONS,
) -> VerifiedSmokeInputs:
    manifest = validate_contract(json.loads(paths.manifest.read_text()), "release")
    if manifest["release_id"] != expected.release_id:
        raise ValueError("ActVision release identity mismatch")
    if hashlib.sha256(canonical_json(manifest).encode()).hexdigest() != expected.bundle_sha256:
        raise ValueError("ActVision bundle SHA256 mismatch")
    if manifest["backbones"]["vision"] != expected.vision_model:
        raise ValueError("Vision encoder identity mismatch")
    if manifest["backbones"]["text"] != expected.text_model:
        raise ValueError("Text encoder identity mismatch")

    vision_snapshot, vision_revision = _verify_vision(paths, expected)
    artifact_paths = {}
    if set(manifest["components"]) != set(expected.components):
        raise ValueError("ActVision component inventory mismatch")
    for name, component_expected in expected.components.items():
        component = manifest["components"].get(name)
        if component is None:
            raise ValueError(f"{name} artifact is missing")
        if component["framework"] != "scikit-learn":
            raise ValueError(f"{name} framework mismatch")
        if component["framework_version"] != expected.framework_version:
            raise ValueError(f"{name} framework version mismatch")
        if component["feature_schema_version"] != component_expected.feature_schema_version:
            raise ValueError(f"{name} feature schema mismatch")
        if component["calibration_version"] != component_expected.calibration_version:
            raise ValueError(f"{name} calibration mismatch")
        if component["sha256"] != component_expected.sha256:
            raise ValueError(f"{name} manifest artifact SHA256 mismatch")
        artifact_path = _safe_child(paths.artifact_root, component["uri"])
        if _sha256_file(artifact_path) != component_expected.sha256:
            raise ValueError(f"{name} artifact SHA256 mismatch")
        artifact_paths[name] = artifact_path

    from provision_semantic_encoder import verify as verify_semantic_encoder

    text = verify_semantic_encoder(paths.text_checkpoint)
    if text.get("model") != expected.text_model:
        raise ValueError("Text encoder identity mismatch")
    if text.get("revision") != expected.text_revision:
        raise ValueError("Text encoder revision mismatch")
    if text.get("checkpoint_sha256") != expected.text_checkpoint_sha256:
        raise ValueError("Text checkpoint SHA256 mismatch")

    return VerifiedSmokeInputs(
        manifest=manifest,
        artifact_paths=artifact_paths,
        vision_snapshot=vision_snapshot,
        vision_revision=vision_revision,
        text_checkpoint=paths.text_checkpoint.resolve(strict=True),
    )


def _runtime_versions() -> dict[str, str]:
    versions = {"python": ".".join(map(str, sys.version_info[:3]))}
    for name in EXPECTED_RUNTIME:
        if name == "python":
            continue
        try:
            versions[name] = package_version(name)
        except PackageNotFoundError:
            versions[name] = "missing"
    return versions


def _load_vision_encoder(snapshot: Path):
    from transformers import AutoModel, AutoProcessor

    processor = AutoProcessor.from_pretrained(
        snapshot, local_files_only=True, trust_remote_code=False
    )
    model = AutoModel.from_pretrained(
        snapshot, local_files_only=True, trust_remote_code=False
    )
    return processor, model


def _load_text_encoder(checkpoint: Path):
    from sentence_transformers import SentenceTransformer

    return SentenceTransformer(
        str(checkpoint),
        device="cpu",
        local_files_only=True,
        trust_remote_code=False,
    )


def _peak_rss_bytes() -> int:
    value = int(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss)
    return value if sys.platform == "darwin" else value * 1024


def load_only(
    inputs: VerifiedSmokeInputs,
    expected: SmokeExpectations = DEFAULT_EXPECTATIONS,
) -> dict:
    started = time.monotonic()
    versions = _runtime_versions()
    for name, required in EXPECTED_RUNTIME.items():
        if versions.get(name) != required:
            raise ValueError(
                f"{name} runtime version mismatch: expected {required}, "
                f"observed {versions.get(name) or 'missing'}"
            )

    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    _load_vision_encoder(inputs.vision_snapshot)
    _load_text_encoder(inputs.text_checkpoint)

    import joblib

    components = {}
    for name, component_expected in expected.components.items():
        path = inputs.artifact_paths[name]
        if _sha256_file(path) != component_expected.sha256:
            raise ValueError(f"{name} artifact changed after verification")
        model = joblib.load(path)
        if not isinstance(model, CalibratedModel):
            raise ValueError(f"{name} artifact is not a supported CalibratedModel")
        if model.feature_schema_version != component_expected.feature_schema_version:
            raise ValueError(f"loaded {name} feature schema mismatch")
        calibration = getattr(getattr(model, "calibrator", None), "version", None)
        if calibration != component_expected.calibration_version:
            raise ValueError(f"loaded {name} calibration mismatch")
        components[name] = {
            "class": f"{type(model).__module__}.{type(model).__qualname__}",
            "feature_schema_version": model.feature_schema_version,
            "calibration_version": calibration,
            "sha256": component_expected.sha256,
        }

    return {
        "release_id": expected.release_id,
        "bundle_sha256": expected.bundle_sha256,
        "runtime": versions,
        "encoders": {
            "vision": {
                "model": expected.vision_model,
                "revision": inputs.vision_revision,
            },
            "text": {
                "model": expected.text_model,
                "revision": expected.text_revision,
                "checkpoint_sha256": expected.text_checkpoint_sha256,
            },
        },
        "components": components,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_bytes": _peak_rss_bytes(),
        "inference_executed": False,
    }

import hashlib
import json
from dataclasses import dataclass, replace
from pathlib import Path

import joblib
import pytest

from actvision_contract import canonical_json
from actvision_load_smoke import (
    DEFAULT_EXPECTATIONS,
    ComponentExpectation,
    EXPECTED_RUNTIME,
    SmokeExpectations,
    SmokePaths,
    load_only,
    verify_inputs,
)
from v2_calibration import CalibratedModel, TemperatureCalibrator


def sha256(value):
    return hashlib.sha256(value).hexdigest()


@dataclass
class InertModel:
    feature_schema_version: str

    def predict(self, *_args, **_kwargs):
        raise AssertionError("load-only smoke invoked prediction")

    def transform(self, *_args, **_kwargs):
        raise AssertionError("load-only smoke invoked transformation")


def write_fixture(tmp_path):
    release_id = "11111111-1111-1111-1111-111111111111"
    revision = "a" * 40
    components = {}
    expected_components = {}
    for name, schema in {
        "vision": "actvision-siglip2-heads-v2",
        "text": "actvision-text-semantic-v1",
        "structured": "actvision-structured-v2",
        "fusion": "actvision-fusion-v2",
    }.items():
        blob = f"{name}-artifact".encode()
        relative = f"models/actvision-v2/{release_id}/{name}.joblib"
        artifact_path = tmp_path / relative
        artifact_path.parent.mkdir(parents=True, exist_ok=True)
        artifact_path.write_bytes(blob)
        component = ComponentExpectation(
            sha256=sha256(blob),
            feature_schema_version=schema,
            calibration_version=f"temperature-v1:{name}",
        )
        expected_components[name] = component
        components[name] = {
            "uri": relative,
            "sha256": component.sha256,
            "framework": "scikit-learn",
            "framework_version": "1.6.1",
            "component_version": f"{release_id}:{name}",
            "feature_schema_version": schema,
            "label_schema_version": "actvision-labels-v2",
            "calibration_version": component.calibration_version,
        }
    manifest = {
        "kind": "release",
        "schema_version": "actvision-v2",
        "label_schema_version": "actvision-labels-v2",
        "release_id": release_id,
        "status": "candidate",
        "components": components,
        "dataset_sha256": "b" * 64,
        "code_commit": "c" * 40,
        "split_policy_version": "actvision-group-split-v2",
        "backbones": {
            "vision": "google/siglip2-base-patch16-224",
            "text": "sentence-transformers/all-MiniLM-L6-v2",
        },
        "thresholds": {},
        "created_at": "2026-10-07T17:58:16.715515+00:00",
        "evaluation": {"report_sha256": "d" * 64, "protected_slices_passed": False},
        "approved_by": None,
        "approved_at": None,
    }
    manifest_path = tmp_path / "release.json"
    manifest_path.write_text(json.dumps(manifest))

    vision = tmp_path / revision
    vision.mkdir()
    weight = b"vision-weights"
    (vision / "model.safetensors").write_bytes(weight)
    provenance_path = tmp_path / "backbone.json"
    provenance_path.write_text(json.dumps({
        "model_id": "google/siglip2-base-patch16-224",
        "revision": revision,
        "snapshot_path": str(vision),
        "weights": {
            "model.safetensors": {"bytes": len(weight), "sha256": sha256(weight)}
        },
    }))

    text = tmp_path / "text"
    text.mkdir()
    expectations = SmokeExpectations(
        release_id=release_id,
        bundle_sha256=sha256(canonical_json(manifest).encode()),
        components=expected_components,
        framework_version="1.6.1",
        vision_model="google/siglip2-base-patch16-224",
        vision_revision=revision,
        text_model="sentence-transformers/all-MiniLM-L6-v2",
        text_revision="e" * 40,
        text_checkpoint_sha256="f" * 64,
    )
    return (
        SmokePaths(manifest_path, tmp_path, provenance_path, text),
        expectations,
        manifest,
    )


def write_load_fixture(tmp_path):
    paths, expected, manifest = write_fixture(tmp_path)
    components = {}
    for name, component in expected.components.items():
        model = CalibratedModel(
            InertModel(component.feature_schema_version),
            TemperatureCalibrator({}, component.calibration_version),
            component.feature_schema_version,
        )
        path = paths.artifact_root / manifest["components"][name]["uri"]
        joblib.dump(model, path, compress=3)
        updated = replace(component, sha256=sha256(path.read_bytes()))
        components[name] = updated
        manifest["components"][name]["sha256"] = updated.sha256
    paths.manifest.write_text(json.dumps(manifest))
    expected = replace(
        expected,
        components=components,
        bundle_sha256=sha256(canonical_json(manifest).encode()),
    )
    return paths, expected


def prepare_load(monkeypatch, paths, expected):
    monkeypatch.setattr(
        "provision_semantic_encoder.verify",
        lambda directory: {
            "model": expected.text_model,
            "revision": expected.text_revision,
            "checkpoint_sha256": expected.text_checkpoint_sha256,
            "directory": str(directory),
        },
    )
    monkeypatch.setattr("actvision_load_smoke._runtime_versions", lambda: EXPECTED_RUNTIME)

    class GuardedEncoder:
        def predict(self, *_args, **_kwargs):
            raise AssertionError("load-only smoke invoked prediction")

        transform = predict
        encode = predict
        embed = predict
        classify = predict
        forward = predict

    def vision_loader(snapshot):
        assert snapshot == paths.vision_provenance.parent / expected.vision_revision
        assert __import__("os").environ["HF_HUB_OFFLINE"] == "1"
        assert __import__("os").environ["TRANSFORMERS_OFFLINE"] == "1"
        return GuardedEncoder(), GuardedEncoder()

    def text_loader(checkpoint):
        assert checkpoint == paths.text_checkpoint
        return GuardedEncoder()

    monkeypatch.setattr("actvision_load_smoke._load_vision_encoder", vision_loader)
    monkeypatch.setattr("actvision_load_smoke._load_text_encoder", text_loader)
    return verify_inputs(paths, expected)


def test_default_expectations_pin_verified_v2_candidate():
    assert DEFAULT_EXPECTATIONS.release_id == "f134590b-122b-42dc-9a27-209523e2a33c"
    assert DEFAULT_EXPECTATIONS.bundle_sha256 == (
        "7527a12908fc62b6a94f86d3337ff5a8ccd25c6d7c50e291a016f5bf4d8dee00"
    )
    assert {
        name: (
            component.sha256,
            component.feature_schema_version,
            component.calibration_version,
        )
        for name, component in DEFAULT_EXPECTATIONS.components.items()
    } == {
        "vision": (
            "95e4f26874850ebd65a90ab17b2731c9f5ec982086e67bf3262eff0864d92c01",
            "actvision-siglip2-heads-v2",
            "temperature-v1:3101ee63de0ef4e0",
        ),
        "text": (
            "79224f3d1037a2df286f14fcc80d40dc7f2a53b77e0b317518c207cf46dc933a",
            "actvision-text-semantic-v1",
            "temperature-v1:74f9fe626b22ef2e",
        ),
        "structured": (
            "21fcac42135029d81941ea05fa12de395ebce9cf1ecc0407472fee31318fa85c",
            "actvision-structured-v2",
            "temperature-v1:6512e85893a6133c",
        ),
        "fusion": (
            "fa87a668c06b4e3251f3499d3ac9a4a72a080cba9f9aa5cb9c86dc09472d0ae5",
            "actvision-fusion-v2",
            "temperature-v1:6df43267c0177965",
        ),
    }
    assert DEFAULT_EXPECTATIONS.framework_version == "1.6.1"
    assert DEFAULT_EXPECTATIONS.vision_revision is None
    assert DEFAULT_EXPECTATIONS.text_revision == "1110a243fdf4706b3f48f1d95db1a4f5529b4d41"
    assert DEFAULT_EXPECTATIONS.text_checkpoint_sha256 == (
        "dfdac870e42d2700ab4813bb3ad1dab8c9f992d4a99d5f50e4f5732610d30d92"
    )


def test_verify_inputs_accepts_matching_local_bundle(tmp_path, monkeypatch):
    paths, expected, _ = write_fixture(tmp_path)
    monkeypatch.setattr(
        "provision_semantic_encoder.verify",
        lambda directory: {
            "model": expected.text_model,
            "revision": expected.text_revision,
            "checkpoint_sha256": expected.text_checkpoint_sha256,
            "directory": str(directory),
        },
    )

    verified = verify_inputs(paths, expected)

    assert verified.manifest["release_id"] == expected.release_id
    assert set(verified.artifact_paths) == {"vision", "text", "structured", "fusion"}
    assert verified.vision_revision == expected.vision_revision


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("release_id", "release identity"),
        ("bundle_sha256", "bundle SHA256"),
        ("artifact_sha256", "vision artifact SHA256"),
        ("calibration", "vision calibration"),
        ("schema", "vision feature schema"),
        ("framework", "vision framework"),
        ("text_revision", "Text encoder revision"),
        ("text_hash", "Text checkpoint SHA256"),
        ("vision_model", "Vision encoder identity"),
        ("vision_revision", "Vision encoder revision"),
    ],
)
def test_verify_inputs_rejects_provenance_mismatch(tmp_path, monkeypatch, mutation, message):
    paths, expected, manifest = write_fixture(tmp_path)
    text_record = {
        "model": expected.text_model,
        "revision": expected.text_revision,
        "checkpoint_sha256": expected.text_checkpoint_sha256,
        "directory": str(paths.text_checkpoint),
    }
    if mutation == "release_id":
        manifest["release_id"] = "22222222-2222-2222-2222-222222222222"
    elif mutation == "bundle_sha256":
        expected = replace(expected, bundle_sha256="0" * 64)
    elif mutation == "artifact_sha256":
        paths.artifact_root.joinpath(manifest["components"]["vision"]["uri"]).write_bytes(b"bad")
    elif mutation == "calibration":
        manifest["components"]["vision"]["calibration_version"] = "wrong"
    elif mutation == "schema":
        manifest["components"]["vision"]["feature_schema_version"] = "wrong"
    elif mutation == "framework":
        manifest["components"]["vision"]["framework_version"] = "0"
    elif mutation == "text_revision":
        text_record["revision"] = "0" * 40
    elif mutation == "text_hash":
        text_record["checkpoint_sha256"] = "0" * 64
    elif mutation == "vision_model":
        record = json.loads(paths.vision_provenance.read_text())
        record["model_id"] = "newer/model"
        paths.vision_provenance.write_text(json.dumps(record))
    elif mutation == "vision_revision":
        record = json.loads(paths.vision_provenance.read_text())
        record["revision"] = "0" * 40
        paths.vision_provenance.write_text(json.dumps(record))
    paths.manifest.write_text(json.dumps(manifest))
    if mutation not in {"bundle_sha256", "artifact_sha256"}:
        expected = replace(expected, bundle_sha256=sha256(canonical_json(manifest).encode()))
    monkeypatch.setattr("provision_semantic_encoder.verify", lambda directory: text_record)

    with pytest.raises(ValueError, match=message):
        verify_inputs(paths, expected)


def test_missing_original_siglip_revision_blocks_before_artifact_reads(tmp_path, monkeypatch):
    paths, expected, _ = write_fixture(tmp_path)
    expected = replace(expected, vision_revision=None)
    reads = []
    original = Path.read_bytes

    def observe(path):
        if str(path).endswith(".joblib"):
            reads.append(path)
        return original(path)

    monkeypatch.setattr(Path, "read_bytes", observe)

    with pytest.raises(ValueError, match="original SigLIP2 revision"):
        verify_inputs(paths, expected)

    assert reads == []


def test_load_only_deserializes_all_compatible_components(tmp_path, monkeypatch):
    paths, expected = write_load_fixture(tmp_path)
    verified = prepare_load(monkeypatch, paths, expected)

    report = load_only(verified, expected)

    assert report["release_id"] == expected.release_id
    assert report["inference_executed"] is False
    assert report["encoders"] == {
        "vision": {"model": expected.vision_model, "revision": expected.vision_revision},
        "text": {
            "model": expected.text_model,
            "revision": expected.text_revision,
            "checkpoint_sha256": expected.text_checkpoint_sha256,
        },
    }
    assert {
        name: (entry["feature_schema_version"], entry["calibration_version"])
        for name, entry in report["components"].items()
    } == {
        name: (component.feature_schema_version, component.calibration_version)
        for name, component in expected.components.items()
    }
    assert report["elapsed_seconds"] >= 0
    assert report["peak_rss_bytes"] > 0


@pytest.mark.parametrize("dependency", sorted(EXPECTED_RUNTIME))
def test_load_only_rejects_runtime_version_mismatch(
    tmp_path, monkeypatch, dependency
):
    paths, expected = write_load_fixture(tmp_path)
    verified = prepare_load(monkeypatch, paths, expected)
    versions = dict(EXPECTED_RUNTIME)
    versions[dependency] = "0"
    monkeypatch.setattr("actvision_load_smoke._runtime_versions", lambda: versions)

    with pytest.raises(ValueError, match=rf"{dependency} runtime version"):
        load_only(verified, expected)


@pytest.mark.parametrize(
    ("mutation", "message"),
    [
        ("class", "supported CalibratedModel"),
        ("schema", "loaded vision feature schema"),
        ("calibration", "loaded vision calibration"),
    ],
)
def test_load_only_rejects_incompatible_artifact(
    tmp_path, monkeypatch, mutation, message
):
    paths, expected = write_load_fixture(tmp_path)
    verified = prepare_load(monkeypatch, paths, expected)
    component = expected.components["vision"]
    if mutation == "class":
        model = InertModel(component.feature_schema_version)
    elif mutation == "schema":
        model = CalibratedModel(
            InertModel("wrong"),
            TemperatureCalibrator({}, component.calibration_version),
            "wrong",
        )
    else:
        model = CalibratedModel(
            InertModel(component.feature_schema_version),
            TemperatureCalibrator({}, "wrong"),
            component.feature_schema_version,
        )
    real_load = joblib.load
    monkeypatch.setattr(
        joblib,
        "load",
        lambda path: model
        if Path(path) == verified.artifact_paths["vision"]
        else real_load(path),
    )

    with pytest.raises(ValueError, match=message):
        load_only(verified, expected)

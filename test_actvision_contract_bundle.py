import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import sys

import pytest

from tools.check_actvision_contract_bundle import build_manifest, check_manifest


REPO_ROOT = Path(__file__).resolve().parent
SOURCE_COMMIT = "68a91e86c13018fed86bb5538cf44002d55cc1fe"
EXPECTED_FILES = {
    "contracts/actvision-v2.schema.json": "d118dd865852280e04a00b031e04f0daf128d95f8e4887b1741578af559d7f8d",
    "actvision_contract.py": "4f5dcdc8e83b8061dd978aaa1b969a1a62176c9a0339835eac003574446e3227",
    "tools/export_actvision_fixtures.py": "53fa3332e7b48705c42c661f1e57094c68a21bc81d9ebb918123548608ec048c",
}
EXPECTED_FIXTURES = {
    "contracts/fixtures/feedback.json": "3bc77a42aafedd00900ef609b24a27fa91ccaa699843397a0319018a8fcf64dd",
    "contracts/fixtures/inference-request-multimodal.json": "545a8bcd3b59be0eaa06643082fd0e36e9cb57c0f846c65a0e3682c0400622d5",
    "contracts/fixtures/inference-request.json": "478672a5ea1bfb47f9ee937b090efdba6db8b89aa72f7160fa374abaf96c8084",
    "contracts/fixtures/prediction-complete.json": "4952672cd41e0774e0283d9fe6ce48142e6080cd624314b6aed5346f1d0dafa7",
    "contracts/fixtures/prediction-unavailable.json": "0bf73a378218b48b8e7ad7ff49605d49023d573d8e4b0c96caa531ea4b53b83b",
    "contracts/fixtures/release-candidate.json": "34692ae264ac91e648c18acfdf032da5ed9c4c112f3ce28983210fd380fd02c2",
    "contracts/fixtures/release-shadow.json": "b3161b3d7b75bdaddeb027318a33189b71bd5d3f41961b314846d83ca511353f",
}


def _manifest(repo_root=REPO_ROOT):
    return json.loads((repo_root / "contracts" / "manifest.json").read_text(encoding="utf-8"))


def _copy_bundle(tmp_path):
    root = tmp_path / "provider"
    shutil.copytree(REPO_ROOT / "contracts", root / "contracts")
    shutil.copy2(REPO_ROOT / "actvision_contract.py", root / "actvision_contract.py")
    (root / "tools").mkdir()
    shutil.copy2(
        REPO_ROOT / "tools" / "export_actvision_fixtures.py",
        root / "tools" / "export_actvision_fixtures.py",
    )
    return root


def test_version_and_manifest_metadata_identify_the_authoritative_bundle():
    manifest = _manifest()

    assert (REPO_ROOT / "contracts" / "VERSION").read_text(encoding="ascii").strip() == "1.0.0"
    assert manifest["bundle_version"] == "1.0.0"
    assert manifest["semantic_contract_version"] == "1.0.0"
    assert manifest["provider"] == "SigLipModel"
    assert manifest["owner"] == "SigLipModel"
    assert manifest["source_commit"] == SOURCE_COMMIT
    assert re.fullmatch(r"[0-9a-f]{40}", manifest["source_commit"])
    assert manifest["wire_version"] == "actvision-v2"
    assert manifest["taxonomy_version"] == "actvision-labels-v2"


def test_manifest_hashes_raw_authoritative_bytes_and_lists_each_fixture_once():
    manifest = _manifest()

    assert manifest["schema"] == {
        "path": "contracts/actvision-v2.schema.json",
        "sha256": EXPECTED_FILES["contracts/actvision-v2.schema.json"],
    }
    assert manifest["semantic_validator"] == {
        "path": "actvision_contract.py",
        "sha256": EXPECTED_FILES["actvision_contract.py"],
    }
    assert manifest["generator"] == {
        "path": "tools/export_actvision_fixtures.py",
        "sha256": EXPECTED_FILES["tools/export_actvision_fixtures.py"],
        "command": "python tools/export_actvision_fixtures.py",
    }
    assert manifest["fixtures"] == [
        {"path": path, "sha256": sha256}
        for path, sha256 in EXPECTED_FIXTURES.items()
    ]

    for entry in [
        manifest["schema"],
        manifest["semantic_validator"],
        manifest["generator"],
        *manifest["fixtures"],
    ]:
        assert hashlib.sha256((REPO_ROOT / entry["path"]).read_bytes()).hexdigest() == entry["sha256"]


def test_manifest_hashes_match_canonical_lf_bytes_at_source_commit():
    manifest = _manifest()
    entries = [
        manifest["schema"],
        manifest["semantic_validator"],
        manifest["generator"],
        *manifest["fixtures"],
    ]

    attributes = subprocess.run(
        ["git", "check-attr", "eol", "--", *(entry["path"] for entry in entries)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.splitlines()
    assert all(line.endswith(": eol: lf") for line in attributes), attributes

    for entry in entries:
        source_bytes = subprocess.run(
            ["git", "show", f"{manifest['source_commit']}:{entry['path']}"],
            cwd=REPO_ROOT,
            capture_output=True,
            check=True,
        ).stdout
        checkout_bytes = (REPO_ROOT / entry["path"]).read_bytes()
        assert b"\r\n" not in checkout_bytes, entry["path"]
        assert checkout_bytes == source_bytes, entry["path"]
        assert hashlib.sha256(source_bytes).hexdigest() == entry["sha256"]


def test_build_manifest_is_deterministic_with_sorted_relative_posix_paths():
    first = build_manifest(REPO_ROOT, SOURCE_COMMIT)
    second = build_manifest(REPO_ROOT, SOURCE_COMMIT)

    assert first == second == _manifest()
    paths = [entry["path"] for entry in first["fixtures"]]
    assert paths == sorted(paths)
    assert all(not Path(path).is_absolute() and "\\" not in path for path in paths)


@pytest.mark.parametrize(
    ("semantic_contract_version", "expected_error"),
    [
        (None, "missing fields semantic_contract_version"),
        ("1.0.1", "semantic_contract_version does not match bundle policy"),
        ("actvision-v2", "semantic_contract_version must be a semantic version"),
    ],
)
def test_checker_rejects_missing_drifted_or_malformed_semantic_contract_version(
    tmp_path,
    semantic_contract_version,
    expected_error,
):
    root = _copy_bundle(tmp_path)
    manifest = _manifest(root)
    if semantic_contract_version is None:
        manifest.pop("semantic_contract_version", None)
    else:
        manifest["semantic_contract_version"] = semantic_contract_version
    (root / "contracts" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    errors = check_manifest(root)

    assert any(expected_error in error for error in errors), errors


def test_checker_rejects_an_unlisted_fixture(tmp_path):
    root = _copy_bundle(tmp_path)
    extra_path = root / "contracts" / "fixtures" / "extra.json"
    extra_path.write_bytes(b'{"synthetic": true}\n')

    errors = check_manifest(root)

    assert any(
        "contracts/fixtures/extra.json" in error and "not listed in manifest" in error
        for error in errors
    )


def test_checker_rejects_a_duplicate_fixture_entry(tmp_path):
    root = _copy_bundle(tmp_path)
    manifest = _manifest(root)
    manifest["fixtures"].append(manifest["fixtures"][0])
    (root / "contracts" / "manifest.json").write_text(
        json.dumps(manifest, indent=2) + "\n",
        encoding="utf-8",
    )

    errors = check_manifest(root)

    assert any(
        "contracts/fixtures/feedback.json" in error and "listed more than once" in error
        for error in errors
    )


@pytest.mark.parametrize(
    "relative_path",
    [
        "contracts/actvision-v2.schema.json",
        "contracts/fixtures/prediction-complete.json",
    ],
)
def test_checker_reports_the_path_when_authoritative_bytes_change(tmp_path, relative_path):
    root = _copy_bundle(tmp_path)
    path = root / relative_path
    path.write_bytes(path.read_bytes() + b"\n")

    errors = check_manifest(root)

    assert any(
        relative_path in error and "SHA256 mismatch" in error
        for error in errors
    )


def test_semantic_fixture_export_remains_valid():
    result = subprocess.run(
        [sys.executable, "tools/export_actvision_fixtures.py", "--check"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert "Validated 7 ActVision v2 fixtures" in result.stdout

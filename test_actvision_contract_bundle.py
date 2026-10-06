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
    "contracts/actvision-v2.schema.json": "8b58809a7261ce33edbc4b590980273b12146dbc4086ea9e4be4252787aaef2f",
    "actvision_contract.py": "7e60fdeed7e178fe2a31280c494e157c1a8f268f4d075a75adc00ca3d47cbfef",
    "tools/export_actvision_fixtures.py": "d6e89db286fe44e14b1a8dd7027bebb0e86027ef71321e50a39e1a270d27f44a",
}
EXPECTED_FIXTURES = {
    "contracts/fixtures/feedback.json": "4b451931e5d443c9fa293d944eb65d9ef5b72b57dde1f4b958ff803c02f900af",
    "contracts/fixtures/inference-request-multimodal.json": "f7dde09a5508a358e31e470e2a8a76cd79d7fcd7b4dfec480879d4c860e9d9a7",
    "contracts/fixtures/inference-request.json": "b9f4f137c040b92b6b61975d7cd0493415da7dfae8d119886c51a31bc65995d1",
    "contracts/fixtures/prediction-complete.json": "48700d2d21895f28647ebf279920e697e69ad33dec7a3d44cc76790d1e8fe3e3",
    "contracts/fixtures/prediction-unavailable.json": "e260bee009471acd9ce2bd61fe7ed5dd4406b295fbe8d794da03bf0bb92a4962",
    "contracts/fixtures/release-candidate.json": "fa93e0f670198f45e284c5f9d0a8a5d17429aac18af416725dac7c6fcfcab5d6",
    "contracts/fixtures/release-shadow.json": "b78bfae61553f2c358dcd84f655503f19cba34798941a5944b8393002ab3bb92",
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


def test_build_manifest_is_deterministic_with_sorted_relative_posix_paths():
    first = build_manifest(REPO_ROOT, SOURCE_COMMIT)
    second = build_manifest(REPO_ROOT, SOURCE_COMMIT)

    assert first == second == _manifest()
    paths = [entry["path"] for entry in first["fixtures"]]
    assert paths == sorted(paths)
    assert all(not Path(path).is_absolute() and "\\" not in path for path in paths)


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

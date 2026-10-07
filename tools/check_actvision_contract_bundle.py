"""Verify the versioned ActVision contract bundle using local raw bytes only."""
from __future__ import annotations

from collections import Counter
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys


BUNDLE_VERSION = "1.0.0"
SEMANTIC_CONTRACT_VERSION = "1.0.0"
PROVIDER = "SigLipModel"
OWNER = "SigLipModel"
WIRE_VERSION = "actvision-v2"
TAXONOMY_VERSION = "actvision-labels-v2"
SOURCE_COMMIT_PATTERN = re.compile(r"[0-9a-f]{40}")
SHA256_PATTERN = re.compile(r"[0-9a-f]{64}")
SEMANTIC_VERSION_PATTERN = re.compile(
    r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)"
)
COMPATIBILITY_POLICY = {
    "policy": "semantic-versioning",
    "compatible_changes": (
        "Optional additive fields or enum behavior that preserve existing valid "
        "payloads and semantics."
    ),
    "breaking_changes": (
        "Required-field, canonicalization, label, identity, status, or "
        "existing-payload validity changes require a separately approved migration."
    ),
    "reference": "contracts/README.md#compatibility-policy",
}
SCHEMA_PATH = "contracts/actvision-v2.schema.json"
SEMANTIC_VALIDATOR_PATH = "actvision_contract.py"
GENERATOR_PATH = "tools/export_actvision_fixtures.py"
GENERATOR_COMMAND = "python tools/export_actvision_fixtures.py"
TOP_LEVEL_FIELDS = {
    "bundle_version",
    "semantic_contract_version",
    "provider",
    "owner",
    "source_commit",
    "wire_version",
    "taxonomy_version",
    "compatibility_policy",
    "schema",
    "semantic_validator",
    "fixtures",
    "generator",
}


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _file_entry(repo_root: Path, relative_path: str) -> dict[str, str]:
    return {
        "path": relative_path,
        "sha256": _sha256(repo_root / relative_path),
    }


def build_manifest(repo_root: Path, source_commit: str) -> dict[str, object]:
    repo_root = Path(repo_root)
    if SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None:
        raise ValueError("source_commit must be a lowercase 40-character Git object ID")

    fixture_paths = sorted(
        path.relative_to(repo_root).as_posix()
        for path in (repo_root / "contracts" / "fixtures").glob("*.json")
    )
    generator = _file_entry(repo_root, GENERATOR_PATH)
    generator["command"] = GENERATOR_COMMAND
    return {
        "bundle_version": BUNDLE_VERSION,
        "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
        "provider": PROVIDER,
        "owner": OWNER,
        "source_commit": source_commit,
        "wire_version": WIRE_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "compatibility_policy": COMPATIBILITY_POLICY,
        "schema": _file_entry(repo_root, SCHEMA_PATH),
        "semantic_validator": _file_entry(repo_root, SEMANTIC_VALIDATOR_PATH),
        "fixtures": [_file_entry(repo_root, path) for path in fixture_paths],
        "generator": generator,
    }


def _read_manifest(repo_root: Path, errors: list[str]) -> dict[str, object] | None:
    path = repo_root / "contracts" / "manifest.json"
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append("contracts/manifest.json: file is missing")
        return None
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        errors.append(f"contracts/manifest.json: cannot read valid UTF-8 JSON ({exc})")
        return None
    if not isinstance(value, dict):
        errors.append("contracts/manifest.json: top level must be an object")
        return None
    return value


def _check_file_entry(
    repo_root: Path,
    manifest: dict[str, object],
    field: str,
    expected_path: str,
    errors: list[str],
    expected_command: str | None = None,
) -> None:
    entry = manifest.get(field)
    if not isinstance(entry, dict):
        errors.append(f"contracts/manifest.json: {field} must be an object")
        return
    if entry.get("path") != expected_path:
        errors.append(
            f"contracts/manifest.json: {field}.path must be {expected_path!r}"
        )
    declared_sha = entry.get("sha256")
    if not isinstance(declared_sha, str) or SHA256_PATTERN.fullmatch(declared_sha) is None:
        errors.append(f"{expected_path}: manifest SHA256 must be 64 lowercase hex characters")
    path = repo_root / expected_path
    try:
        actual_sha = _sha256(path)
    except OSError as exc:
        errors.append(f"{expected_path}: cannot read raw bytes ({exc})")
    else:
        if declared_sha != actual_sha:
            errors.append(
                f"{expected_path}: SHA256 mismatch "
                f"(manifest {declared_sha!r}, actual {actual_sha})"
            )
    if expected_command is not None and entry.get("command") != expected_command:
        errors.append(
            f"contracts/manifest.json: {field}.command must be {expected_command!r}"
        )


def _valid_fixture_path(path: object) -> bool:
    if not isinstance(path, str) or "\\" in path:
        return False
    pure_path = PurePosixPath(path)
    return (
        not pure_path.is_absolute()
        and ".." not in pure_path.parts
        and len(pure_path.parts) == 3
        and pure_path.parts[:2] == ("contracts", "fixtures")
        and pure_path.suffix == ".json"
    )


def _check_fixtures(
    repo_root: Path,
    manifest: dict[str, object],
    errors: list[str],
) -> None:
    fixtures = manifest.get("fixtures")
    if not isinstance(fixtures, list):
        errors.append("contracts/manifest.json: fixtures must be an array")
        return

    entries_by_path: dict[str, list[dict[str, object]]] = {}
    manifest_paths: list[str] = []
    for index, entry in enumerate(fixtures):
        if not isinstance(entry, dict):
            errors.append(f"contracts/manifest.json: fixtures[{index}] must be an object")
            continue
        path = entry.get("path")
        if not _valid_fixture_path(path):
            errors.append(
                f"contracts/manifest.json: fixtures[{index}].path must be a relative POSIX "
                "contracts/fixtures/*.json path"
            )
            continue
        manifest_paths.append(path)
        entries_by_path.setdefault(path, []).append(entry)

    for path, count in sorted(Counter(manifest_paths).items()):
        if count != 1:
            errors.append(f"{path}: listed more than once in manifest")
    if manifest_paths != sorted(manifest_paths):
        errors.append("contracts/manifest.json: fixture paths must be sorted")

    actual_paths = sorted(
        path.relative_to(repo_root).as_posix()
        for path in (repo_root / "contracts" / "fixtures").glob("*.json")
    )
    actual_set = set(actual_paths)
    manifest_set = set(manifest_paths)
    for path in sorted(actual_set - manifest_set):
        errors.append(f"{path}: fixture is not listed in manifest")
    for path in sorted(manifest_set - actual_set):
        errors.append(f"{path}: manifest fixture is missing from disk")

    for path in sorted(actual_set & manifest_set):
        entry = entries_by_path[path][0]
        declared_sha = entry.get("sha256")
        if not isinstance(declared_sha, str) or SHA256_PATTERN.fullmatch(declared_sha) is None:
            errors.append(f"{path}: manifest SHA256 must be 64 lowercase hex characters")
        try:
            actual_sha = _sha256(repo_root / path)
        except OSError as exc:
            errors.append(f"{path}: cannot read raw bytes ({exc})")
        else:
            if declared_sha != actual_sha:
                errors.append(
                    f"{path}: SHA256 mismatch "
                    f"(manifest {declared_sha!r}, actual {actual_sha})"
                )


def check_manifest(repo_root: Path) -> list[str]:
    repo_root = Path(repo_root)
    errors: list[str] = []
    manifest = _read_manifest(repo_root, errors)
    if manifest is None:
        return errors

    missing_fields = sorted(TOP_LEVEL_FIELDS - set(manifest))
    extra_fields = sorted(set(manifest) - TOP_LEVEL_FIELDS)
    if missing_fields:
        errors.append(
            f"contracts/manifest.json: missing fields {', '.join(missing_fields)}"
        )
    if extra_fields:
        errors.append(
            f"contracts/manifest.json: unexpected fields {', '.join(extra_fields)}"
        )

    try:
        version = (repo_root / "contracts" / "VERSION").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as exc:
        errors.append(f"contracts/VERSION: cannot read ASCII version ({exc})")
    else:
        if version != manifest.get("bundle_version"):
            errors.append(
                "contracts/VERSION: version does not match manifest bundle_version"
            )
        if version != BUNDLE_VERSION:
            errors.append(f"contracts/VERSION: expected {BUNDLE_VERSION!r}, found {version!r}")

    expected_metadata = {
        "bundle_version": BUNDLE_VERSION,
        "semantic_contract_version": SEMANTIC_CONTRACT_VERSION,
        "provider": PROVIDER,
        "owner": OWNER,
        "wire_version": WIRE_VERSION,
        "taxonomy_version": TAXONOMY_VERSION,
        "compatibility_policy": COMPATIBILITY_POLICY,
    }
    semantic_contract_version = manifest.get("semantic_contract_version")
    if (
        not isinstance(semantic_contract_version, str)
        or SEMANTIC_VERSION_PATTERN.fullmatch(semantic_contract_version) is None
    ):
        errors.append(
            "contracts/manifest.json: semantic_contract_version must be a "
            "semantic version"
        )
    for field, expected in expected_metadata.items():
        if manifest.get(field) != expected:
            errors.append(
                f"contracts/manifest.json: {field} does not match bundle policy"
            )

    source_commit = manifest.get("source_commit")
    if (
        not isinstance(source_commit, str)
        or SOURCE_COMMIT_PATTERN.fullmatch(source_commit) is None
    ):
        errors.append(
            "contracts/manifest.json: source_commit must be a lowercase "
            "40-character Git object ID"
        )

    _check_file_entry(repo_root, manifest, "schema", SCHEMA_PATH, errors)
    _check_file_entry(
        repo_root,
        manifest,
        "semantic_validator",
        SEMANTIC_VALIDATOR_PATH,
        errors,
    )
    _check_file_entry(
        repo_root,
        manifest,
        "generator",
        GENERATOR_PATH,
        errors,
        expected_command=GENERATOR_COMMAND,
    )
    _check_fixtures(repo_root, manifest, errors)
    return errors


def main() -> int:
    repo_root = Path(__file__).resolve().parents[1]
    errors = check_manifest(repo_root)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    manifest = json.loads(
        (repo_root / "contracts" / "manifest.json").read_text(encoding="utf-8")
    )
    print(
        f"Validated ActVision contract bundle {manifest['bundle_version']} "
        f"with {len(manifest['fixtures'])} fixtures"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

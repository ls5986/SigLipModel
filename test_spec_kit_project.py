import json
import re
from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent
FEATURE_DIRECTORY = PROJECT_ROOT / "specs" / "001-actvision-v2-provider-foundation"
STANDARD_ARTIFACTS = (
    "spec.md",
    "research.md",
    "data-model.md",
    "contracts/README.md",
    "quickstart.md",
    "plan.md",
    "checklists/requirements.md",
    "tasks.md",
)


def test_spec_kit_provider_project_is_initialized():
    assert (PROJECT_ROOT / ".specify" / "memory" / "constitution.md").is_file()
    assert (PROJECT_ROOT / ".specify" / "templates").is_dir()
    assert (PROJECT_ROOT / ".specify" / "scripts" / "powershell").is_dir()

    github_root = PROJECT_ROOT / ".github"
    assert any(
        path.is_file()
        for path in (github_root / "skills").glob("speckit-*/SKILL.md")
    )

    for relative_path in STANDARD_ARTIFACTS:
        assert (FEATURE_DIRECTORY / relative_path).is_file(), relative_path

    feature_state = json.loads(
        (PROJECT_ROOT / ".specify" / "feature.json").read_text(encoding="utf-8")
    )
    assert (
        feature_state["feature_directory"].replace("\\", "/")
        == "specs/001-actvision-v2-provider-foundation"
    )


def test_provider_constitution_enforces_ml_and_product_boundaries():
    constitution = (
        PROJECT_ROOT / ".specify" / "memory" / "constitution.md"
    ).read_text(encoding="utf-8").lower()

    for principle in (
        "temporal leakage",
        "immutable evidence",
        "missing modalities",
        "axis-specific provenance",
        "protected-group integrity",
        "exact release identity",
        "explicit promotion",
        "canonical mls",
        "ranking",
    ):
        assert principle in constitution


def test_foundation_spec_defines_stable_provider_requirements():
    specification = (FEATURE_DIRECTORY / "spec.md").read_text(encoding="utf-8")

    for requirement_family in ("ML", "DATA", "TIME", "API", "SEC", "REL", "OPS"):
        assert re.search(rf"\b{requirement_family}-\d{{3}}\b", specification)

    for requirement_id, subject in {
        "ML-001": "labels",
        "DATA-001": "datasets",
        "ML-002": "training",
        "ML-003": "evaluation",
        "REL-001": "releases",
        "API-001": "inference",
        "API-002": "feedback",
        "API-003": "contract compatibility",
    }.items():
        assert requirement_id in specification
        assert subject in specification.lower()


def test_requirements_checklist_is_owned_by_reviewers():
    checklist = (
        FEATURE_DIRECTORY / "checklists" / "requirements.md"
    ).read_text(encoding="utf-8")
    checklist_items = [
        line for line in checklist.splitlines() if line.startswith("- [ ]")
    ]

    assert checklist_items
    assert all("[Reviewer]" in item for item in checklist_items)


def test_future_tasks_separate_experiment_from_multimodal_release():
    tasks = (FEATURE_DIRECTORY / "tasks.md").read_text(encoding="utf-8")

    assert "Experimental Text/Metadata Candidate (No Release)" in tasks
    assert "Full Multimodal Release (Future Work)" in tasks
    assert "- [x]" not in tasks.lower()

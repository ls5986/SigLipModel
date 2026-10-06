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


def test_training_provenance_distinguishes_isolated_experiments_from_releases():
    constitution = (
        PROJECT_ROOT / ".specify" / "memory" / "constitution.md"
    ).read_text(encoding="utf-8").lower()
    specification = (FEATURE_DIRECTORY / "spec.md").read_text(encoding="utf-8")
    research = (FEATURE_DIRECTORY / "research.md").read_text(encoding="utf-8")
    tasks = (FEATURE_DIRECTORY / "tasks.md").read_text(encoding="utf-8")

    assert "experimental/non-release candidates" in constitution
    ml_002 = " ".join(
        specification.split("**ML-002", 1)[1].split("**ML-003", 1)[0].lower().split()
    )
    assert "experimental/non-release candidates" in ml_002
    assert "release-eligible training" in ml_002
    assert "experimental/non-release candidates" in research.lower()
    assert "experimental/non-release candidates" in tasks.lower()


def test_evidence_snapshot_identity_includes_release():
    data_model = (FEATURE_DIRECTORY / "data-model.md").read_text(encoding="utf-8")
    evidence_snapshot = " ".join(
        data_model.split("### EvidenceSnapshot", 1)[1]
        .split("### LabelRevision", 1)[0]
        .lower()
        .split()
    )

    assert "`release_id`" in evidence_snapshot
    assert "release changes create a new `evidence_id`" in evidence_snapshot


def test_requirements_checklist_is_owned_by_reviewers():
    checklist = (
        FEATURE_DIRECTORY / "checklists" / "requirements.md"
    ).read_text(encoding="utf-8")
    checklist_items = [
        line for line in checklist.splitlines() if line.startswith("- [ ]")
    ]

    assert checklist_items
    assert all("[Reviewer]" in item for item in checklist_items)
    assert not re.search(r"(?m)^- \[[xX]\]", checklist)


def test_future_tasks_separate_experiment_from_multimodal_release():
    tasks = (FEATURE_DIRECTORY / "tasks.md").read_text(encoding="utf-8")

    assert "Experimental Text/Metadata Candidate (No Release)" in tasks
    assert "Full Multimodal Release (Future Work)" in tasks
    assert "- [x]" not in tasks.lower()


def test_ci_enforces_provider_project_and_bundle_before_backend_regressions():
    workflow = (
        PROJECT_ROOT / ".github" / "workflows" / "ci.yml"
    ).read_text(encoding="utf-8")
    backend_regression = workflow.index("Backend regression suite")

    for required_check in (
        "tools/check_actvision_contract_bundle.py",
        "test_spec_kit_project.py",
        "test_actvision_contract_bundle.py",
    ):
        assert required_check in workflow
        assert workflow.index(required_check) < backend_regression

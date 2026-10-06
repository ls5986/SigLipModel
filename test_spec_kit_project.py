from pathlib import Path


PROJECT_ROOT = Path(__file__).resolve().parent


def test_spec_kit_provider_project_is_initialized():
    assert (PROJECT_ROOT / ".specify" / "memory" / "constitution.md").is_file()
    assert (PROJECT_ROOT / ".specify" / "templates").is_dir()
    assert (PROJECT_ROOT / ".specify" / "scripts" / "powershell").is_dir()

    github_root = PROJECT_ROOT / ".github"
    assert any(
        path.is_file()
        for path in (github_root / "skills").glob("speckit-*/SKILL.md")
    )

    assert (
        PROJECT_ROOT
        / "specs"
        / "001-actvision-v2-provider-foundation"
        / "spec.md"
    ).is_file()

# SigLipModel Spec Kit Provider Project Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Initialize SigLipModel as the authoritative ActVision provider Spec Kit project and publish one versioned, offline-verifiable contract bundle for MLSSourcing.

**Architecture:** Spec Kit owns provider principles and feature artifacts under `.specify/` and `specs/`. The existing JSON Schema, semantic validator, and fixtures remain authoritative; a manifest and version layer make the bundle consumable across repositories. Provider CI validates semantics and raw-byte hashes without coupling to a consumer checkout.

**Tech Stack:** Specify CLI, PowerShell Spec Kit scripts, Markdown, Python 3.12, pytest, JSON Schema, SHA256, GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-10-06-spec-kit-provider-project-design.md`

## Global Constraints

- Initialize only in an isolated Git worktree created from the latest live provider branch.
- Use `specify init --here --force --integration copilot --script ps`; inspect the generated diff before retaining files.
- Do not train, infer, promote, deploy, migrate, or modify Supabase/Render state.
- Keep `contracts/actvision-v2.schema.json` authoritative and preserve semantic checks in `actvision_contract.py`.
- Do not break current fixtures or legacy v1 artifacts.
- Provider contract updates are backward-compatible unless an explicit migration spec says otherwise.
- CI contract checks must be deterministic and offline.
- All commits include `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>`.

## Review Focus

- Initialization in the non-empty repository must not overwrite runtime, model, contract, or review files.
- The manifest must cover every fixture exactly once and reject extra untracked fixtures.
- Hash validation must use raw bytes so newline or formatting changes require an intentional bundle update.
- Foundation specs must distinguish experimental candidates from approved releases.
- The provider constitution must prohibit writes to MLS canonical economics and ranking.

---

### Task 1: Initialize the SigLipModel Spec Kit project

**Files:**
- Create/modify from Spec Kit: `.specify/**`
- Create/modify from Copilot integration: `.github/**`
- Create: `test_spec_kit_project.py`

**Interfaces:**
- Consumes: Specify CLI `specify init --here --force --integration copilot --script ps`.
- Produces: a directory-scoped Spec Kit project rooted at SigLipModel with PowerShell scripts and Copilot skills.

- [ ] **Step 1: Write the failing project-structure test**

Create `test_spec_kit_project.py` with
`test_spec_kit_provider_project_is_initialized()`. Assert that:

- `.specify/memory/constitution.md` exists;
- `.specify/templates` and `.specify/scripts/powershell` exist;
- at least one generated Copilot `speckit` skill or command file exists under `.github`;
- `specs/001-actvision-v2-provider-foundation/spec.md` exists.

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest test_spec_kit_project.py -q`

Expected: FAIL because `.specify/` and `specs/` do not exist.

- [ ] **Step 3: Install and initialize Spec Kit in the isolated worktree**

Run:

```powershell
uv tool install specify-cli
specify init --here --force --integration copilot --script ps
```

Inspect `git status --short`. Retain only the GitHub Copilot integration and
Spec Kit project files; preserve all pre-existing files byte-for-byte.

- [ ] **Step 4: Run the project-structure test**

Run: `python -m pytest test_spec_kit_project.py -q`

Expected: FAIL only because the provider foundation artifacts are not authored yet.

- [ ] **Step 5: Commit the initialization**

```powershell
git add .specify .github test_spec_kit_project.py
git commit -m "chore: initialize ActVision Spec Kit project" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 2: Author the provider constitution and foundation feature

**Files:**
- Modify: `.specify/memory/constitution.md`
- Create: `specs/001-actvision-v2-provider-foundation/spec.md`
- Create: `specs/001-actvision-v2-provider-foundation/research.md`
- Create: `specs/001-actvision-v2-provider-foundation/data-model.md`
- Create: `specs/001-actvision-v2-provider-foundation/contracts/README.md`
- Create: `specs/001-actvision-v2-provider-foundation/quickstart.md`
- Create: `specs/001-actvision-v2-provider-foundation/plan.md`
- Create: `specs/001-actvision-v2-provider-foundation/checklists/requirements.md`
- Create: `specs/001-actvision-v2-provider-foundation/tasks.md`
- Modify: `.specify/feature.json` if generated feature state requires activation
- Modify: `test_spec_kit_project.py`

**Interfaces:**
- Consumes: the approved SDD blueprint, current ActVision README/contract docs, and deployed provider architecture.
- Produces: stable `ML-`, `DATA-`, `TIME-`, `API-`, `SEC-`, `REL-`, and `OPS-` requirements.

- [ ] **Step 1: Extend the failing test for provider principles and artifacts**

Assert:

- constitution principles include temporal leakage prevention, immutable
  evidence, unknown/missing-modality semantics, axis-specific provenance,
  protected-group integrity, exact release identity, explicit promotion, and no
  canonical MLS ranking writes;
- `spec.md` contains stable requirements for labels, datasets, training,
  evaluation, releases, inference, feedback, and contract compatibility;
- all standard artifacts exist;
- requirements checklist items are reviewer-owned;
- tasks clearly separate experimental text/metadata work from full multimodal releases.

- [ ] **Step 2: Run the test and verify authored-content assertions fail**

Run: `python -m pytest test_spec_kit_project.py -q`

Expected: FAIL on missing principles and artifacts.

- [ ] **Step 3: Write the provider constitution**

Use normative language and define amendment/versioning rules. State that
SigLipModel owns provider behavior and MLSSourcing owns product economics/ranking.

- [ ] **Step 4: Write the breadth-first provider `spec.md`**

Focus on desired behavior and safety. Include user/operator journeys,
non-goals, measurable release criteria, evidence/provenance requirements, and
acceptance scenarios. Record current experimental candidate limitations without
promoting them to approved behavior.

- [ ] **Step 5: Write provider supporting artifacts**

Capture existing-model research and open decisions in `research.md`; define
conceptual evidence/dataset/run/artifact/release entities in `data-model.md`;
define provider contract ownership in `contracts/README.md`; explain how to start
a new ML feature using Spec Kit in `quickstart.md`.

- [ ] **Step 6: Write technical plan, reviewer checklist, and tasks**

The plan references frozen SigLIP2, semantic text, structured evidence, fusion,
calibration, protected evaluation, storage, Supabase, and hosted workers. The
checklist evaluates requirement quality. Tasks decompose future correctness and
deployment work without claiming it is complete.

- [ ] **Step 7: Activate the provider foundation feature**

Ensure `.specify/feature.json` selects
`specs/001-actvision-v2-provider-foundation`.

- [ ] **Step 8: Run the provider project test**

Run: `python -m pytest test_spec_kit_project.py -q`

Expected: PASS.

- [ ] **Step 9: Commit the provider foundation**

```powershell
git add .specify/memory/constitution.md .specify/feature.json specs/001-actvision-v2-provider-foundation test_spec_kit_project.py
git commit -m "docs: define ActVision provider specification" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 3: Version the authoritative ActVision contract bundle

**Files:**
- Create: `contracts/VERSION`
- Create: `contracts/CHANGELOG.md`
- Create: `contracts/manifest.json`
- Create: `tools/check_actvision_contract_bundle.py`
- Create: `test_actvision_contract_bundle.py`
- Modify: `contracts/README.md`
- Modify: `specs/001-actvision-v2-provider-foundation/contracts/README.md`

**Interfaces:**
- Consumes: `contracts/actvision-v2.schema.json`,
  `contracts/fixtures/*.json`, `actvision_contract.py`, and
  `tools/export_actvision_fixtures.py`.
- Produces:

```python
def build_manifest(repo_root: Path, source_commit: str) -> dict[str, object]
def check_manifest(repo_root: Path) -> list[str]
def main() -> int
```

- [ ] **Step 1: Write failing bundle tests**

Tests cover:

- `VERSION` matches the manifest wire bundle version;
- manifest provider and source commit are valid;
- schema hash matches raw bytes;
- every fixture appears exactly once and hashes match raw bytes;
- an extra fixture fails;
- changed schema or fixture bytes produce path-specific errors;
- semantic fixture export remains valid.

- [ ] **Step 2: Run tests and verify they fail**

Run: `python -m pytest test_actvision_contract_bundle.py -q`

Expected: FAIL because version, manifest, and checker do not exist.

- [ ] **Step 3: Implement the bundle builder/checker**

Use sorted relative POSIX paths in the manifest. Do not rewrite schema or fixture
content. The checker is offline and does not invoke GitHub.

- [ ] **Step 4: Choose and document the initial bundle version**

Use `1.0.0` for the existing stable `actvision-v2` wire contract. Record the
current provider commit at execution time, semantic contract version, schema,
all seven fixtures, generator command, compatibility policy, and owner.

- [ ] **Step 5: Update contract documentation**

Document semantic versioning, compatible additions, breaking-change migration,
provider-first rollout, consumer pinning, and rollback order.

- [ ] **Step 6: Run bundle and semantic validation**

Run:

```powershell
python tools/export_actvision_fixtures.py --check
python tools/check_actvision_contract_bundle.py
python -m pytest test_actvision_contract_bundle.py test_actvision_v2.py -q
```

Expected: PASS.

- [ ] **Step 7: Commit the authoritative bundle**

```powershell
git add contracts tools/check_actvision_contract_bundle.py test_actvision_contract_bundle.py specs/001-actvision-v2-provider-foundation/contracts/README.md
git commit -m "feat: version ActVision contract bundle" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 4: Enforce provider specs and bundle consistency in CI

**Files:**
- Modify: `.github/workflows/ci.yml`
- Modify: `README.md`
- Modify: `test_spec_kit_project.py`

**Interfaces:**
- Consumes: project and bundle checks from Tasks 2 and 3.
- Produces: required CI failures for Spec Kit drift, fixture drift, or manifest drift.

- [ ] **Step 1: Add a failing workflow-content test**

Assert CI runs `tools/check_actvision_contract_bundle.py`,
`test_spec_kit_project.py`, and `test_actvision_contract_bundle.py`.

- [ ] **Step 2: Run the test and verify it fails**

Run: `python -m pytest test_spec_kit_project.py -q`

Expected: FAIL because CI does not include all new checks.

- [ ] **Step 3: Add explicit project and bundle validation to CI**

Keep existing compile, fixture, Python, and browser jobs. Run the bundle checker
before the broader backend regression suite.

- [ ] **Step 4: Link the provider Spec Kit project and contract bundle from `README.md`**

Explain active feature state, bundle versioning, MLSSourcing consumer ownership,
and the full production Spec Kit workflow.

- [ ] **Step 5: Run targeted validation**

Run:

```powershell
python tools/export_actvision_fixtures.py --check
python tools/check_actvision_contract_bundle.py
python -m pytest test_spec_kit_project.py test_actvision_contract_bundle.py test_actvision_v2.py -q
```

Expected: PASS.

- [ ] **Step 6: Commit CI enforcement**

```powershell
git add .github/workflows/ci.yml README.md test_spec_kit_project.py
git commit -m "ci: enforce provider specs and contract bundle" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

### Task 5: Verify provider-project convergence

**Files:**
- Modify only if convergence finds gaps: `specs/001-actvision-v2-provider-foundation/tasks.md`

**Interfaces:**
- Consumes: all prior tasks and the provider foundation spec.
- Produces: a Spec Kit project with no untracked conversion gaps.

- [ ] **Step 1: Run Spec Kit consistency analysis**

Use `/speckit-analyze` on the active provider foundation. Resolve findings in
the originating artifact, then rerun.

- [ ] **Step 2: Run the complete provider gate**

Run:

```powershell
python tools/export_actvision_fixtures.py --check
python tools/check_actvision_contract_bundle.py
python -m pytest test_spec_kit_project.py test_actvision_contract_bundle.py test_actvision_v2.py -q
git status --short
```

Expected: checks pass and only intentional convergence edits remain.

- [ ] **Step 3: Run `/speckit-converge` after implementation**

Expected: `Converged`, or append-only tasks for concrete missing provider
foundation work. Implement appended tasks and rerun until converged.

- [ ] **Step 4: Commit convergence artifacts if changed**

```powershell
git add specs/001-actvision-v2-provider-foundation/tasks.md
git commit -m "docs: converge ActVision provider foundation spec" -m "Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>"
```

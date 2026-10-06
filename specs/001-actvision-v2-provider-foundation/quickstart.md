# Quickstart: Start an ActVision ML Feature

Use this workflow for provider labels, datasets, models, evaluation, releases,
inference, feedback, or contract changes. It creates documentation and tests; it
does not authorize training, deployment, migrations, paid calls, or external
system writes.

## 1. Confirm the boundary

Before creating a feature, write one sentence for each:

- provider behavior SigLipModel will own;
- product economics/ranking MLSSourcing continues to own;
- exact evidence time boundary;
- affected contract and legacy behavior; and
- external side effects that remain prohibited.

If the change writes canonical MLS scores/ranks or treats later outcomes as
acquisition evidence, redesign it before continuing.

## 2. Create the feature

From the repository root in an isolated worktree:

```powershell
.\.specify\scripts\powershell\create-new-feature.ps1 `
  -ShortName "short-feature-name" `
  "Describe the provider behavior, evidence boundary, and success criteria"
```

The generated `.specify/feature.json` is the checkout-local authoritative pointer
for Spec Kit scripts. Verify that `feature_directory` names the intended directory
under `specs\`. Do not copy another worktree's absolute path.

To select an existing feature for one command session:

```powershell
$env:SPECIFY_FEATURE_DIRECTORY = "specs\002-short-feature-name"
.\.specify\scripts\powershell\check-prerequisites.ps1 -PathsOnly
```

## 3. Specify behavior before implementation

Use the generated GitHub Copilot Spec Kit skills in this order:

1. `speckit-specify` — user journeys, acceptance scenarios, stable requirements,
   non-goals, and measurable success criteria;
2. `speckit-clarify` — resolve material ambiguity without weakening safety;
3. `speckit-plan` — research, conceptual data, contracts, and technical plan;
4. `speckit-checklist` — reviewer-owned requirement-quality checks;
5. `speckit-tasks` — RED/GREEN implementation tasks; and
6. `speckit-analyze` — cross-artifact consistency before implementation.

Every provider requirement uses a stable family:

- `ML-` labels, model components, training, evaluation, and calibration;
- `DATA-` evidence, provenance, datasets, and revisions;
- `TIME-` point-in-time and era safeguards;
- `API-` inference, feedback, and contract compatibility;
- `SEC-` credentials, privacy, access, and artifact trust;
- `REL-` release identity, criteria, promotion, rollback, and retirement;
- `OPS-` bounded workers, failure behavior, and ownership boundaries.

Reference the foundation requirement rather than copying it with altered meaning.

## 4. Pass the Constitution Check

The feature plan must answer:

- How are temporal leakage and exact evidence identity prevented?
- How are UNKNOWN and missing modalities preserved?
- What is the axis-specific human provenance?
- How are physical groups and protected labels isolated?
- Which exact dataset, code, artifacts, evaluation, and contract identify a
  candidate or release?
- Which explicit operator action changes release state?
- How does the design preserve legacy v1 and avoid canonical MLS ranking writes?
- Which operations are offline/read-only during ordinary tests?

Any unresolved answer blocks implementation or narrows the feature to an
experiment with no release claim.

## 5. Implement with TDD

For each task:

1. write one test for the required behavior;
2. run it and verify the expected failure;
3. implement the smallest provider change;
4. run the focused test and the relevant regression suite; and
5. record exact verification and any approved ruling.

Tests use synthetic data and deterministic offline checks. A test MUST NOT call a
paid model, load private records, train an expensive model, deploy, apply a
migration, promote, or modify an external system.

## 6. Separate experiment from release

An experiment may compare a frozen encoder, text/metadata head, aggregation,
calibrator, or fusion design. Its outputs remain candidate-only and state their
limitations. A release additionally requires:

- current human-per-axis labels and independent group coverage;
- immutable dataset/run/artifact identities;
- pre-registered per-axis and protected-slice thresholds;
- verified contract and semantic fixtures;
- trusted compatible runtime adapters; and
- explicit authorized shadow/production promotion.

Finish with `speckit-converge` only after implementation and all required tests.
Convergence does not itself train, approve, promote, or deploy a model.

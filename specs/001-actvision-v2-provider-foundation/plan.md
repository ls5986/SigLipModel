# Implementation Plan: ActVision v2 Provider Foundation

**Branch**: `001-actvision-v2-provider-foundation` | **Date**: 2026-10-06 |
**Spec**: [spec.md](spec.md)

**Input**: Stable provider requirements for evidence, labels, grouped datasets,
multimodal model components, protected evaluation, exact releases, inference,
feedback, and contract compatibility.

## Summary

Build ActVision as an evidence-bound provider with four independently identified
model components: frozen SigLIP2 vision, supervised semantic text, allowlisted
structured evidence, and calibrated fusion. Human-approved, axis-specific labels
feed immutable grouped datasets. Grouped out-of-fold component predictions feed
fusion and calibration. Protected human evaluation and pre-registered criteria
gate explicit release transitions. Supabase stores revisioned workspace records
and private evidence; separate hosted workers perform bounded model work. The
authoritative v2 schema and semantic validator remain unchanged unless a later
contract feature explicitly updates them.

This foundation plan documents the target and its gates. It does not claim the
full provider is implemented, trained, approved, deployed, or migrated.

## Technical Context

**Language/Version**: Python 3.12; PowerShell Spec Kit scripts; JavaScript only
for existing browser checks

**Primary Dependencies**: frozen SigLIP2 backbone, pinned local semantic text
encoder, NumPy/SciPy/scikit-learn model heads, JSON Schema validation, existing
provider modules and hosted adapters

**Storage**: immutable local artifact directories plus revisioned workspace data
and private media in Supabase; no storage change in this feature

**Testing**: pytest, deterministic contract/fixture checks, synthetic grouped
datasets, existing browser tests, and deployment acceptance only in a separately
approved rollout

**Target Platform**: local model-capable Windows development workers and
least-privilege hosted Linux workers behind the existing authenticated provider
surface

**Project Type**: existing Python web service, review application, and separated
background worker system

**Performance Goals**: Numeric latency, throughput, and resource budgets MUST be
pre-registered by the deployment feature after representative shadow measurement;
no unmeasured production target is invented here

**Constraints**: offline deterministic CI; no private data or model weights in
Git; no future-event leakage; exact evidence/release identity; group isolation;
protected labels excluded from fitting; explicit promotion; legacy v1 preserved;
no canonical MLS economics/ranking writes

**Scale/Scope**: current imported workspace and future incoming listing snapshots,
with throughput and capacity validated before any consumer enablement

## Current State and Target State

| Area | Current state | Required target |
|---|---|---|
| Vision | Frozen SigLIP2 embeddings, draft context labels, legacy/property experiments | Supervised property components with chosen grouped aggregation and protected evaluation |
| Text | TF-IDF baseline and provisioned frozen semantic encoder path | Reviewed semantic heads with exact span provenance, calibration, and abstention |
| Structured | Allowlisted acquisition facts and experimental heads | Versioned feature policy with temporal tests and supported-class metrics |
| Fusion | Component availability and experimental fusion utilities | Grouped OOF fusion with missing-modality evaluation and calibration |
| Labels | Typed human revisions plus machine drafts | Sufficient independent human-per-axis coverage for declared release scope |
| Release | Explicit registry/loader gates; no approved full-v2 artifacts | Verified candidate bundle and explicit shadow/production transitions |
| Inference | Honest 503 artifact/adapter gate | Compatible approved release adapter honoring requested modes |
| Contract | Authoritative v2 schema, semantic rules, synthetic fixtures | Versioned raw-byte bundle and provider-first consumer pinning |
| Operations | Lightweight hosted review plus separate workers | Measured, least-privilege, bounded workers and rollback acceptance |

## Constitution Check

*GATE: Required before research and rechecked after conceptual design.*

| Principle | Plan evidence | Gate |
|---|---|---|
| Point-in-time evidence | `TIME-001/002`; source timestamp and era in `EvidenceSnapshot`; temporal feature-policy tests | PASS |
| Immutable identity | Content-addressed evidence, dataset, run, artifact, evaluation, release, and contract bundle | PASS |
| Unknown/missing modalities | Independent UNKNOWN masks; explicit missing/unavailable/failed component states; no silent fallback | PASS |
| Axis-specific provenance | One `LabelRevision` per axis and evidence revision; proposals remain distinct | PASS |
| Protected-group integrity | Physical-property grouping, grouped OOF training, protected labels read only for final evaluation | PASS |
| Exact release and explicit promotion | Immutable Release composition and audited candidate/shadow/production/retired transitions | PASS |
| Provider/product boundary | SigLipModel produces physical evidence; MLSSourcing owns economics/ranking and canonical writes | PASS |
| Bounded secure operations | Workspace-scoped storage, trusted artifact verification, bounded workers, sanitized failures | PASS |

Post-design recheck: **PASS**. No constitutional exception is proposed.

## Architecture

### 1. Evidence and labels

The provider derives an `EvidenceSnapshot` from exact acquisition-time remarks,
allowlisted structured facts, selected photos, exclusions, and source/era
identity. Human review creates independent `LabelRevision` records. Draft model
proposals remain inputs to review, not labels. Any relevant evidence change
invalidates eligibility until reviewed again.

### 2. Dataset construction

Preview computes eligibility without writing a dataset. Explicit freeze creates a
content-identified `DatasetVersion` containing label/evidence revisions, physical
groups, train/validation/protected assignments, exclusions, and protected-slice
membership. Dataset validation rejects alias overlap, temporal violations, stale
evidence, unknown-as-negative conversion, and protected fitting access.

### 3. Component training

- **Vision**: keep the SigLIP2 checkpoint frozen. Compare declared property-bag
  aggregations on grouped validation only, then fit bounded supervised heads.
- **Semantic text**: keep the pinned encoder frozen, chunk long remarks, retain
  exact human evidence spans, and train heads only for adequately covered
  PRESENT/ABSENT classes. UNKNOWN is masked.
- **Structured evidence**: apply a versioned allowlist and acquisition-time
  transformation policy. Reject arbitrary fields and future outcomes.
- **Fusion**: create component predictions out-of-fold by physical group. Fit
  fusion using component outputs and availability/coverage, never protected data.
- **Calibration**: fit and select per declared head/mode on non-protected data.
  Unsupported or unreliable outputs abstain.

Each run pins code, dataset, configuration, dependency lock, seeds, frozen
encoder/backbone identity, inputs, outputs, and resource bounds.

### 4. Evaluation and release

Before protected evaluation, the release owner registers numeric thresholds for
each supported axis/class, calibration criterion, abstention behavior, and
required protected slice. Final evaluation reads the protected labels once under
the declared policy and records coverage as well as metrics. Missing required
coverage cannot pass.

A candidate bundle contains explicit vision, text, structured, and fusion slots;
null means unavailable. Loader checks establish trust, path containment, exact
bytes, framework/feature/label/calibration compatibility, and operator approval.
An explicit authorized action moves a candidate to shadow or production. Retirement
and rollback preserve historical identities.

### 5. Contract, inference, and feedback

`contracts/actvision-v2.schema.json` and `actvision_contract.py` remain the
behavioral authority. A raw-byte manifest packages their exact identity and every
synthetic fixture for offline consumer checks.

Inference authenticates the service caller, validates evidence, loads only the
pinned approved release, honors explicit modes without fallback, and returns
component states and exact observed evidence. Feedback is idempotent,
append-only, workspace-bound, and pending human review. Neither flow writes
canonical MLS economics, scores, ranks, or conditions.

### 6. Storage and workers

Supabase may hold revisioned workspace records and private evidence under existing
row/storage restrictions. Local or hosted workers use separate least-privilege
credentials and bounded leases. Review pages and hosted web processes do not load
large model runtimes. Result commits are idempotent so a worker can recover an
already-written immutable artifact after an interrupted final status write.

Any migration, hosted-worker deployment, secret change, shadow connection, or
production enablement is a separate approved task with backup, acceptance, and
rollback evidence.

## Data Flow

```text
source snapshot + media + human review
               |
               v
 EvidenceSnapshot --> axis LabelRevisions
               |             |
               +------ eligibility preview
                              |
                    explicit DatasetVersion freeze
                              |
       +----------------------+----------------------+
       |                      |                      |
 frozen SigLIP2 head   semantic text head    structured head
       |                      |                      |
       +------ grouped out-of-fold predictions -----+
                              |
                   fusion + calibration
                              |
             protected EvaluationRecord
                              |
               explicit Release transition
                              |
     validated inference --> shadow consumer
                              |
                 append-only feedback
                              |
                    human review only
```

## Error Handling

- Stale evidence, invalid source era, group overlap, or future feature: reject
  before freeze and identify the non-secret failing record/policy.
- Missing model or dependency: mark unavailable and keep the inference gate
  closed; never load a legacy substitute.
- Changed artifact bytes or incompatibility: reject before deserialization.
- Protected criterion failure or missing coverage: block promotion.
- Worker lease interruption: recover only an exact verified result or fail the
  run; paid/external work is never retried implicitly.
- Database, migration, storage, or network failure: return sanitized unavailable
  status; never fall back to stale local data.
- Feedback retry: return the prior event for identical content; reject conflicting
  content with the same event ID.

## Verification Strategy

1. **Requirement/project checks**: validate standard Spec Kit artifacts, stable
   IDs, reviewer-owned quality checklist, active feature, and experimental/release
   separation.
2. **Unit tests**: canonical identity, temporal feature rejection, unknown masks,
   grouped splits, artifact verification, and state transitions.
3. **Contract tests**: schema plus semantic validation, exact raw-byte manifest
   hashes, fixture coverage, and legacy compatibility.
4. **Integration tests**: synthetic preview/freeze/run/evaluate/bundle/infer/
   feedback paths with no private data or external side effects.
5. **Protected evaluation**: separately approved execution using preregistered
   human-per-axis criteria; results become immutable release evidence.
6. **Deployment acceptance**: separately approved staging check for identity,
   authentication, least privilege, throughput, recovery, shadow behavior, and
   rollback before any production decision.

## Project Structure

### Documentation for this feature

```text
specs/001-actvision-v2-provider-foundation/
├── spec.md
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── README.md
├── checklists/
│   └── requirements.md
└── tasks.md
```

### Existing implementation boundaries

```text
contracts/
├── actvision-v2.schema.json
└── fixtures/

actvision_contract.py
v2_dataset.py
v2_training.py
v2_calibration.py
v2_release.py
v2_inference.py
v2_runtime.py
text_model.py
structured_model.py
fusion_model.py
property_models.py
cloud_training.py
hosted_label_worker.py
supabase/
tools/
test_*.py
```

**Structure Decision**: Evolve the existing modules and tests by responsibility;
do not create a second provider service or replace the authoritative contract.

## Delivery Phases

1. Provider foundation and offline contract consistency.
2. Experimental text/metadata correctness work with no release claim.
3. Full multimodal dataset, component, fusion, calibration, and protected
   evaluation.
4. Trusted release bundle, inference, and feedback compatibility.
5. Separately approved hosted-worker and consumer-shadow rollout.

Detailed future work and dependencies are in [tasks.md](tasks.md). Every item is
unchecked because this foundation feature does not claim future implementation.

## Complexity Tracking

No constitution violation or additional project is justified.

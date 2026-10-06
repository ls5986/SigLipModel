# Data Model: ActVision v2 Provider Foundation

This is a conceptual model. It defines identity, relationships, and invariants;
it does not prescribe a database migration or authorize storage changes.

## Identity Rules

- Stable IDs are immutable references, not mutable display names.
- Content identities use exact canonical bytes defined by the owning contract.
- A revision points to its predecessor or superseded event; it never overwrites it.
- Workspace, physical-property group, source listing/era, and temporal snapshot
  are independent dimensions and must not be inferred from one another.
- A relationship to an entity is valid only when its recorded identity still
  resolves to the same bytes and semantics.

## Entities

### EvidenceSnapshot

Represents the exact point-in-time evidence offered to a reviewer or model.

| Field | Meaning |
|---|---|
| `evidence_id` | Content identity over the complete canonical snapshot |
| `workspace_id` | Authorized data boundary |
| `property_id` / `property_group_id` | Source property and deduplicated physical group |
| `listing_id` / `source_snapshot_at` | Listing/era and temporal boundary |
| `visual_generation` / `photos_changed_at` | Source-media revision markers |
| `selected_photos[]` | Ordered photo IDs, URI hashes, known byte hashes, exclusions |
| `remarks_hash` | Hash of exact UTF-8 remarks bytes |
| `structured_hash` | Hash of exact canonical structured evidence |
| `modality_state` | Present/missing state for vision, text, and structured inputs |
| `created_at` | Audit time; not a substitute for source time |

**Invariants**:

- Changed selected order, bytes, remarks, structured facts, source time, or
  exclusions creates a new `evidence_id`.
- URI identity does not substitute for an image-byte hash.
- Later events cannot be added to an earlier snapshot.

### LabelRevision

Represents one explicit judgment for one axis and evidence revision.

| Field | Meaning |
|---|---|
| `label_revision_id` | Immutable revision identity |
| `axis` | Physical condition, modernization, acquisition fit, or one text signal |
| `value` | Axis-domain value including UNKNOWN |
| `evidence_id` | Evidence reviewed for this judgment |
| `reviewer_id` / `reviewed_at` | Human approval provenance |
| `support` | Exact snippets, selected photos, reasons, and confidence as applicable |
| `proposal_identity` | Optional draft model/policy/input that the human reviewed |
| `supersedes_revision_id` | Optional prior revision |
| `status` | Approved, retracted, or superseded |

**Invariants**:

- Approval is axis-specific. One LabelRevision cannot approve another axis.
- UNKNOWN stays masked; non-mention does not become ABSENT.
- Stale evidence cannot be eligible for a new dataset freeze.

### DatasetVersion

Represents an immutable, point-in-time training/evaluation manifest.

| Field | Meaning |
|---|---|
| `dataset_id` / `version` / `fingerprint` | Stable identity and content fingerprint |
| `created_by` / `created_at` | Explicit freeze actor and audit time |
| `policy_version` | Eligibility, feature-time, grouping, and split rules |
| `examples[]` | Evidence and eligible LabelRevision identities |
| `groups[]` | Physical-property and evidence-alias membership |
| `splits` | Train, validation, and protected-test group assignments |
| `protected_slices` | Versioned slice definitions and immutable memberships |
| `exclusions[]` | Rejected example and explicit reason |
| `parent_dataset_id` | Optional prior version used for comparison only |

**Invariants**:

- Each group appears in exactly one split.
- Freeze reads current approved labels and evidence but copies neither into a
  mutable pointer.
- Protected labels are unavailable to fitting and selection.
- Any changed label, evidence, policy, group, split, or slice changes the
  fingerprint.

### ModelRun

Represents one bounded training, calibration, evaluation, or reproducibility run.

| Field | Meaning |
|---|---|
| `run_id` / `run_kind` | Immutable run identity and declared purpose |
| `dataset_id` | Exact frozen dataset consumed |
| `code_commit` / `config_hash` / `dependency_lock_hash` | Executable identity |
| `random_seeds` / `determinism_policy` | Reproduction controls |
| `component` | Vision, text, structured, fusion, calibration, or evaluation |
| `input_artifact_ids[]` | Frozen encoders or prior OOF component outputs |
| `stages[]` | Append-only start/success/failure/recovery events |
| `output_artifact_ids[]` | Exact outputs |
| `resource_bounds` | Time, memory, concurrency, and budget ceilings |

**Invariants**:

- Training runs consume train/validation only as declared.
- Fusion and calibration consume group-out-of-fold component predictions.
- Evaluation reads protected labels only in the final declared stage.
- A retry either recovers the same immutable result or creates a new run identity;
  it cannot silently replace output bytes.

### Artifact

Represents a content-addressed, trusted run output or frozen dependency.

| Field | Meaning |
|---|---|
| `artifact_id` / `sha256` / `size_bytes` | Exact-byte identity |
| `artifact_kind` | Encoder, feature policy, model head, calibrator, report, or manifest |
| `uri` | Operator-provisioned location; not identity by itself |
| `framework` / `framework_version` | Loader compatibility |
| `feature_version` / `label_version` / `calibration_version` | Semantic compatibility |
| `producer_run_id` | Creating run or verified external provenance |
| `trust_record` | Approval for origin and executable format |

**Invariants**:

- The loader verifies path containment, bytes, schema, compatibility, and trust
  before deserializing.
- A changed artifact is a new Artifact, even if its filename is unchanged.

### EvaluationRecord

Represents immutable model-quality evidence for one candidate composition.

| Field | Meaning |
|---|---|
| `evaluation_id` | Identity over candidate, dataset, metrics, and criteria |
| `artifact_ids[]` | Exact components evaluated |
| `dataset_id` / `protected_slice_ids[]` | Exact human-labeled evaluation inputs |
| `criteria_version` | Pre-registered numeric thresholds |
| `metrics` | Per-axis, per-class, calibration, abstention, and slice results |
| `coverage` | Independent groups and unsupported classes/modalities |
| `decision` | Pass/fail by criterion without aggregate override |
| `reviewed_by` / `reviewed_at` | Human quality review |

**Invariants**:

- Criteria are registered before protected metrics are read.
- Missing required coverage is a failure or unsupported release scope, never a
  passing zero.

### Release

Represents an approved provider composition and compatibility identity.

| Field | Meaning |
|---|---|
| `release_id` | Exact immutable release identity |
| `status` | Candidate, shadow, production, or retired |
| `components` | Explicit vision, text, structured, and fusion Artifact references or null |
| `dataset_id` / `code_commit` | Training lineage |
| `evaluation_id` | Required quality evidence |
| `wire_version` / `label_version` | Contract compatibility |
| `approval` | Operator, time, source state, target state, and rationale |
| `transitions[]` | Append-only state history |

**Invariants**:

- Artifact creation creates at most a candidate; it never promotes.
- Shadow/production require verified bytes, compatibility, evaluation pass, and
  explicit authorization.
- Retirement preserves prior predictions and does not delete artifacts.

### Prediction

Represents an immutable inference result.

| Field | Meaning |
|---|---|
| `prediction_id` | Immutable result identity |
| `request_id` / `evidence_id` / `release_id` | Complete binding |
| `requested_mode` / `mode_used` | No silent fallback |
| `component_results` | Status and output for each declared component |
| `physical_result` | Axis values, probabilities, confidence, and text signals |
| `observed_photo_hashes` | Real bytes used at execution |
| `created_at` | Inference audit time |

**Invariants**:

- Non-available components contain UNKNOWN/null outputs and a reason.
- Prediction values cannot mutate labels, economics, scores, or ranking.

### FeedbackEvent

Represents a correction proposal linked to an immutable Prediction.

| Field | Meaning |
|---|---|
| `event_id` | Caller-provided idempotency identity |
| `prediction_id` / `evidence_id` / `release_id` | Original context |
| `reviewer_id` / `workspace_id` | Authorized provenance |
| `correction` | Axis-specific proposed correction and support |
| `supersedes_event_id` | Optional prior feedback event |
| `review_status` | Pending, approved into a later LabelRevision, or rejected |
| `content_hash` | Detects conflicting reuse of `event_id` |

**Invariants**:

- Exact retries return the same accepted event; different content with the same
  ID conflicts.
- Pending feedback never edits the source prediction or frozen dataset.
- Training requires a later explicit human-approved LabelRevision and new dataset.

## Relationships

```text
EvidenceSnapshot 1 ── * LabelRevision
EvidenceSnapshot * ── * DatasetVersion (through immutable example entries)
DatasetVersion   1 ── * ModelRun
ModelRun         1 ── * Artifact
Artifact         * ── * EvaluationRecord
EvaluationRecord 1 ── * Release (one evaluation may support scoped transitions)
Release          1 ── * Prediction
EvidenceSnapshot 1 ── * Prediction
Prediction       1 ── * FeedbackEvent
FeedbackEvent    0..1 ── 0..1 LabelRevision (only after explicit human review)
```

## State Transitions

### Dataset

`preview -> frozen`

Preview is read-only and mutable by recomputation. Freeze is explicit and
immutable. There is no unfreeze; corrections produce a new version.

### Run

`queued -> running -> succeeded | failed | cancelled`

Recovery may attach a verified already-written output to the same idempotent run.
It does not repeat paid or externally visible work implicitly.

### Release

`candidate -> shadow -> production -> retired`

An authorized operator may retire shadow or production and may promote a
previously approved compatible release through a new recorded transition. States
are not skipped without requirements and approval for the target state.

### Feedback

`pending -> approved | rejected`

Approval creates or supports a new LabelRevision; it does not mutate the
Prediction. A correction after a decision uses a new event and explicit
supersession.

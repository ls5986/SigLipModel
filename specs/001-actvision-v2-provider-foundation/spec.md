# Feature Specification: ActVision v2 Provider Foundation

**Feature Branch**: `001-actvision-v2-provider-foundation`

**Created**: 2026-10-06

**Status**: Foundation approved for planning; provider implementation and release
approval remain incomplete

**Input**: Establish the durable behavioral, safety, evidence, contract, and
release requirements for the ActVision ML provider without training, inference,
promotion, deployment, migration, or external-system modification.

## User Scenarios & Testing

### User Story 1 - Review an Evidence-Bound Label (Priority: P1)

A trained reviewer evaluates only the evidence available for one property and
era, records physical condition, modernization, acquisition fit, and semantic
text signals independently, and may leave any unsupported axis UNKNOWN.

**Why this priority**: Honest, independently sourced labels are the basis for
every later dataset, metric, model, and release decision.

**Independent Test**: Use synthetic evidence with one missing modality, save a
review, and verify that each approved axis retains the reviewer and exact
evidence identity while unsupported axes remain UNKNOWN.

**Acceptance Scenarios**:

1. **Given** verified acquisition-era remarks and no usable interior images,
   **When** a reviewer approves supported text signals but leaves physical
   condition unknown, **Then** the text approvals are retained and physical
   condition remains UNKNOWN rather than becoming a negative.
2. **Given** a saved review and changed photo bytes or remarks, **When** the
   review is read for training eligibility, **Then** the prior evidence identity
   no longer matches and a new review revision is required.
3. **Given** an AI proposal, **When** the reviewer saves a correction, **Then**
   the human decision, proposal identity, correction, and axis-specific evidence
   remain distinguishable in append-only history.

---

### User Story 2 - Build and Evaluate a Reproducible Candidate (Priority: P1)

An ML operator freezes an eligible point-in-time dataset, runs declared component
training, calibration, and protected evaluation, and receives an immutable
candidate whose limits are explicit.

**Why this priority**: A candidate cannot be trusted or compared unless its data,
code, groups, artifacts, and evaluation are reproducible.

**Independent Test**: Build a synthetic manifest twice from identical inputs and
verify identical identities; change one label, evidence byte, split assignment,
or dependency and verify a new identity is required.

**Acceptance Scenarios**:

1. **Given** photos and listings linked to the same physical property, **When**
   the dataset is frozen, **Then** every alias is assigned to exactly one of
   training, validation, or protected test.
2. **Given** protected labels, **When** training, fusion, calibration, threshold
   selection, and model selection run, **Then** no protected label is read until
   the final declared evaluation stage.
3. **Given** an unsupported class or modality, **When** the candidate is
   evaluated, **Then** the report marks it unavailable or UNKNOWN and does not
   infer quality from a related class or aggregate.

---

### User Story 3 - Approve and Operate an Exact Release (Priority: P1)

An authorized release operator reviews per-axis and protected-slice results,
verifies every artifact and compatibility identity, and explicitly transitions a
candidate to shadow or production. The operator can retire it without rewriting
historical predictions.

**Why this priority**: Trained artifacts and experimental predictions must never
be mistaken for approved provider behavior.

**Independent Test**: Attempt each release transition with missing approval,
failed slice criteria, changed artifact bytes, and valid evidence; only the valid
explicit transition succeeds and all attempts are audited.

**Acceptance Scenarios**:

1. **Given** a trained candidate with no recorded protected evaluation, **When**
   production promotion is requested, **Then** the request fails closed.
2. **Given** an approved shadow release and exact artifact hashes, **When** the
   operator explicitly promotes it, **Then** the transition records actor, time,
   source and target states, evaluation identity, and immutable release identity.
3. **Given** a retired release, **When** an old prediction is inspected, **Then**
   its original evidence and release identities remain available.

---

### User Story 4 - Consume Provider Results Without Product Writes (Priority: P2)

MLSSourcing validates a pinned ActVision contract bundle, requests a supported
mode, stores predictions in shadow, and submits corrections as append-only
feedback. It owns any later economics or ranking decision.

**Why this priority**: The provider/consumer boundary prevents physical-evidence
predictions from silently becoming investment or ranking decisions.

**Independent Test**: Validate synthetic request, prediction, release, and
feedback fixtures against the authoritative schema and semantic validator, then
verify that no flow writes canonical MLS economics, condition, score, or rank.

**Acceptance Scenarios**:

1. **Given** an explicit `images_and_metadata` request with no compatible vision
   artifact, **When** inference is requested, **Then** the provider returns an
   honest unavailable response and does not silently use metadata-only mode.
2. **Given** the same feedback event ID and body, **When** it is retried, **Then**
   the provider returns the original idempotent result; a different body using
   that ID is rejected as a conflict.
3. **Given** a compatible prediction, **When** MLSSourcing consumes it, **Then**
   the prediction remains shadow evidence and product economics and ranking stay
   under MLSSourcing policy.

### Edge Cases

- A property appears under multiple source rows, listing IDs, or image aliases.
- The source timestamp is known but the acquisition photo era is uncertain.
- Remarks are present while images are missing, expired, changed, synthetic, or
  explicitly excluded.
- A text signal is not mentioned; non-mention remains UNKNOWN, not ABSENT.
- A reviewer retracts or supersedes one axis without changing other axes.
- One class meets aggregate coverage while a protected modality slice has no
  independent groups.
- The requested release exists but an artifact hash, feature version, framework,
  or calibration identity differs from the local approved manifest.
- A worker finishes its artifact write but loses its final status response.
- The same feedback event is delivered more than once or supersedes a prior event.
- A candidate produces plausible output but has never met human-per-axis release
  criteria.

## Requirements

### Label and Model Requirements

- **ML-001 — Labels**: The provider MUST maintain independent label domains for
  physical condition, modernization, acquisition fit, and each semantic text
  signal. UNKNOWN MUST be valid for every unsupported judgment and MUST NOT be
  treated as a negative.
- **ML-002 — Training**: Training MUST consume only an immutable frozen dataset,
  declared code/configuration/dependency identities, and approved labels. It MUST
  preserve physical-property groups and MUST NOT read protected labels while
  fitting components, fusion, calibration, thresholds, or model selection.
- **ML-003 — Evaluation**: Evaluation MUST report human-per-axis metrics,
  supported-class coverage, calibration where applicable, and independently
  declared protected slices. Aggregate performance MUST NOT mask a failed or
  absent slice.
- **ML-004 — Components**: The intended full provider MUST represent frozen
  SigLIP2 vision, supervised semantic text, allowlisted structured evidence, and
  fusion as separately identified optional components. A component not trained
  and provisioned for the release MUST be unavailable, not synthesized.
- **ML-005 — Calibration and Abstention**: Probabilities and confidence MUST be
  published only when their calibration method, dataset, and metrics are pinned.
  The provider MUST abstain or return UNKNOWN when declared evidence or acceptance
  support is insufficient.

### Dataset, Evidence, and Time Requirements

- **DATA-001 — Datasets**: Datasets MUST be immutable, content-identified
  manifests of examples, axis-specific labels, evidence identities, source/era
  decisions, property groups, split assignments, inclusion/exclusion reasons,
  and protected-slice definitions.
- **DATA-002 — Evidence**: Evidence MUST identify workspace, property, listing,
  source snapshot, selected photos in order, known image bytes, URI identities,
  remarks bytes, structured facts, exclusions, and modality availability.
- **DATA-003 — Revisions**: Approved labels, feedback, predictions, dataset
  freezes, and release transitions MUST be append-only. Corrections MUST create
  revisions or superseding events and MUST NOT rewrite prior evidence.
- **DATA-004 — Provenance**: Every approved label MUST retain reviewer, time,
  evidence identity, axis, value, supporting evidence, and any source proposal.
  Property approval and photo approval MUST remain separate.
- **TIME-001 — Temporal Boundary**: Features and labels MUST use only information
  available at the declared snapshot. Current acquisition closes, later sales,
  later renovations, later media, and later outcomes MUST be excluded from an
  earlier acquisition-time input.
- **TIME-002 — Era Integrity**: Unknown or conflicting listing/photo eras MUST
  fail training eligibility without changing known-target or review history.

### API and Contract Requirements

- **API-001 — Inference**: Inference MUST validate the request, pin exact evidence
  and release identities, honor the requested mode without silent fallback, and
  identify every component as available, missing, unavailable, or failed.
- **API-002 — Feedback**: Feedback MUST validate the original prediction,
  evidence, release, reviewer, and workspace; exact retries MUST be idempotent;
  conflicting reuse MUST fail; accepted events MUST remain pending human review
  rather than becoming automatic training truth.
- **API-003 — Contract Compatibility**: Contract compatibility MUST be defined by
  the authoritative `contracts/actvision-v2.schema.json`, semantic rules in
  `actvision_contract.py`, wire/taxonomy versions, and checked synthetic fixtures.
  Compatible additions MUST preserve existing consumers and legacy v1 artifacts;
  breaking changes require a separately approved migration feature.
- **API-004 — Consumer Binding**: Consumers MUST validate schema and semantic
  rules, compare evidence and release identities to their request and pinned
  manifest, and reject mismatches or fabricated defaults.

### Security, Release, Reliability, and Operations Requirements

- **SEC-001 — Data and Credentials**: Private records, media, credentials,
  reviews, and model artifacts MUST remain outside source control and client
  output. Service access MUST be authenticated, least-privilege, encrypted
  outside local tests, and restricted to the configured workspace.
- **SEC-002 — Artifact Safety**: Artifact loading MUST verify trusted origin,
  path containment, schema, compatibility, and exact bytes before
  deserialization. A hash alone MUST NOT authorize untrusted executable formats.
- **REL-001 — Releases**: Releases MUST have exact immutable identities that pin
  component artifacts, hashes, framework and feature versions, label and
  calibration versions, dataset, code, evaluation, and approval.
- **REL-002 — Promotion**: Candidate, shadow, production, and retired transitions
  MUST be explicit, authorized, audited, and reversible by pinning a previously
  approved compatible release. Training or review saves MUST NOT promote.
- **REL-003 — Release Criteria**: Shadow and production promotion MUST require all
  pre-registered per-axis and protected-slice thresholds to pass, every required
  artifact to verify, and all supported response fixtures to satisfy contract and
  semantic validation.
- **REL-004 — Historical Integrity**: Retirement or rollback MUST NOT delete or
  rewrite historical releases, predictions, evidence, feedback, or evaluations.
- **OPS-001 — Workers**: Training and inference workers MUST be bounded,
  idempotent where retry is permitted, observable by non-secret stages, and able
  to recover an already-written immutable result after an interrupted status
  update.
- **OPS-002 — Failure Behavior**: Missing dependencies, models, migrations,
  storage, or service connectivity MUST fail closed with sanitized, actionable
  status. Failures MUST NOT fall back to stale local state or another model.
- **OPS-003 — Side Effects**: Reading, previewing, opening a page, saving a review,
  or starting the application MUST NOT implicitly train, infer, promote, deploy,
  migrate, enqueue paid work, or modify external systems.
- **OPS-004 — Ownership Boundary**: SigLipModel MUST own provider behavior and
  contract artifacts. MLSSourcing MUST own economics, opportunity qualification,
  and ranking. The provider MUST NOT write canonical MLS scores, ranks,
  economics, or condition fields.

## Evidence and Provenance Rules

1. Exact remarks bytes and exact structured JSON are hashed before feature
   normalization. Selected photo order and known image-byte hashes are retained;
   a URI hash is not evidence that image bytes are unchanged.
2. Evidence revisions are content-addressed and preserve the prior revision.
   Changed remarks, facts, exclusions, selected photos, image bytes, source
   timestamp, or release create a new identity.
3. Human approval is per axis and per evidence revision. Draft model proposals
   retain their own model/policy/input identity and never masquerade as approval.
4. Dataset and run manifests link identities rather than copying mutable labels or
   pointers. A reproducibility check fails when any linked bytes or identities
   differ.

## Current Experimental Candidate Status

The existing hosted experimental candidate is limited to supervised semantic
text and allowlisted acquisition metadata using a pinned frozen encoder and
logistic heads. It may use explicitly selected unreviewed AI drafts while keeping
human corrections higher priority. It does **not** use photographs as inputs,
train SigLIP2, calibrate probabilities, train late fusion, provide a compatible
full v2 physical release, or satisfy production promotion criteria. Its
predictions remain experimental and separate from approved reviews and releases.
This specification records those limitations; it does not approve or promote the
candidate.

## Non-Goals

- Training or evaluating a real model as part of this foundation feature.
- Performing provider inference, paid labeling, bulk enqueue, or backfill.
- Promoting, deploying, retiring, or rolling back any release.
- Applying Supabase migrations, modifying Render, changing credentials, or
  writing any external system.
- Defining or writing MLS product economics, opportunity scores, or ranking.
- Replacing `contracts/actvision-v2.schema.json`, weakening semantic validation,
  or modifying legacy v1 artifacts.
- Treating current draft labels, experimental predictions, imported cohorts, or
  positive similarity as approved physical truth.

## Key Entities

- **Evidence Snapshot**: Exact point-in-time multimodal input and exclusions.
- **Label Revision**: One axis-specific human judgment bound to evidence.
- **Dataset Version**: Immutable grouped split and eligibility manifest.
- **Model Run**: Reproducible training or evaluation execution.
- **Artifact**: Content-addressed component, calibration, or report output.
- **Release**: Approved composition of exact artifacts and compatibility data.
- **Prediction**: Immutable result bound to evidence, mode, and release.
- **Feedback Event**: Append-only correction proposal linked to a prediction.

Detailed conceptual fields and transitions are in [data-model.md](data-model.md).

## Success Criteria

### Measurable Outcomes

- **SC-001**: 100% of accepted labels, predictions, feedback events, datasets,
  runs, artifacts, and releases resolve to immutable evidence or input identities;
  any changed input produces a detectable identity mismatch.
- **SC-002**: Dataset validation reports zero physical-property or evidence-alias
  overlap among training, validation, and protected-test splits.
- **SC-003**: 100% of UNKNOWN labels and missing/unavailable/failed components
  remain masked from negative-label counts and publish no fabricated probability,
  score, or confidence.
- **SC-004**: 100% of supported request, prediction, feedback, and release
  fixtures pass both JSON Schema and cross-field semantic validation; invalid or
  mismatched fixtures fail deterministically offline.
- **SC-005**: Every promoted release has passing pre-registered thresholds for
  each supported human-labeled axis and every required protected slice, with no
  missing required artifact or unverifiable byte.
- **SC-006**: Re-running a declared deterministic dataset/run build from identical
  inputs produces the same manifest identities and compatible outputs within the
  pre-registered numeric tolerance.
- **SC-007**: Provider integration tests perform zero canonical MLS economics,
  opportunity-score, condition, or ranking writes.

## Assumptions

- The existing v2 JSON Schema and semantic validator remain authoritative.
- Private evidence, labels, and artifacts live in approved local or Supabase
  storage and are referenced by identity rather than committed.
- Numeric metric thresholds are registered by a future model feature before its
  first protected evaluation; this foundation does not invent thresholds without
  reviewed data.
- Current runtime gates continue returning unavailable responses until compatible
  approved artifacts and adapters exist.
- Legacy v1 behavior and artifacts remain unchanged unless a separate migration
  specification is approved.

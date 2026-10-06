# SigLipModel ActVision Provider Constitution

## Core Principles

### I. Point-in-Time Evidence Only

Every label, dataset row, model input, prediction, and evaluation example MUST be
bound to the evidence that was available at its declared decision timestamp.
Acquisition-time models MUST prevent temporal leakage: later sales, renovations,
reviews, outcomes, and replacement media MUST NOT influence an earlier snapshot.
Source timestamps and the applicable listing/photo era MUST be retained so an
independent reviewer can reproduce the temporal boundary.

### II. Immutable Evidence and Reproducible Identity

Approved evidence, frozen datasets, training runs, artifacts, evaluations, and
releases MUST have immutable identities derived from their exact inputs. An
evidence identity MUST change when selected photos, photo bytes, remarks,
structured facts, source snapshots, or release inputs change. Frozen records MUST
be append-only; corrections create a new revision rather than rewriting history.
Every candidate and release MUST pin dataset, code, configuration, dependency,
artifact, and evaluation identities. Mutable names and branch names are not
release identities.

### III. Unknown and Missing Modalities Are Honest States

UNKNOWN means unassessed or unsupported and MUST NOT be converted to a negative.
Missing modalities MUST remain explicit as `missing`; trained components that are
not provisioned MUST be `unavailable`; execution failures MUST be `failed`.
Models and adapters MUST NOT fabricate evidence, confidence, labels, or fallback
results. An explicitly requested inference mode MUST fail honestly when it cannot
be satisfied rather than silently substituting another mode.

### IV. Axis-Specific Provenance and Semantics

Physical condition, modernization, acquisition fit, semantic text signals, and
product economics are distinct constructs. Every approved label MUST retain
axis-specific provenance: reviewer, timestamp, evidence identity, source proposal
when present, confidence, and supporting evidence appropriate to that axis.
Approval of a property, photo, or one axis MUST NOT approve another. Non-mention
in remarks is UNKNOWN, not ABSENT, and exact text evidence MUST remain verbatim.

### V. Protected-Group Integrity

All photos, listings, transactions, and aliases for one physical property MUST
remain in one split. Duplicate or linked evidence MUST NOT cross training,
validation, or protected-test boundaries. Protected-group integrity is
non-negotiable: protected labels MUST NOT fit features, heads, fusion,
calibration, thresholds, or model selection. Evaluation MUST report independently
defined missing-modality, temporal, evidence-quality, and difficult-negative
slices without hiding unsupported classes.

### VI. Exact Release Identity and Explicit Promotion

Every served prediction MUST name an exact release identity and full evidence
identity. A model artifact, successful training run, or saved review is not a
release. Candidate, shadow, production, and retired are explicit states; every
transition requires an explicit promotion or retirement action, an authorized
operator, recorded evaluation evidence, and an auditable timestamp. No workflow
may train, promote, deploy, or roll back implicitly. Historical predictions and
release records remain immutable after a transition.

### VII. Authoritative Contract and Product Boundary

SigLipModel owns ActVision provider behavior: label semantics, evidence identity,
dataset construction, model training, calibration, evaluation, artifacts,
releases, inference, feedback, and the wire contract. The checked-in
`contracts/actvision-v2.schema.json` remains authoritative, together with the
cross-field rules in `actvision_contract.py`; legacy v1 artifacts remain valid.

MLSSourcing owns product economics, opportunity qualification, and ranking.
SigLipModel MUST NOT write canonical MLS economics, opportunity scores, condition
fields, or ranking. Provider outputs are physical-evidence observations that a
consumer may validate and use in shadow under its own approved product policy.

### VIII. Bounded, Secure, and Observable Execution

Workers MUST be least-privilege, workspace-bound, idempotent where retry is
allowed, and bounded by explicit time, size, concurrency, and budget limits.
Credentials, private records, media, model weights, and review exports MUST NOT
enter source control or logs. Artifact loaders MUST verify schema, compatibility,
path containment, and exact bytes before trusted deserialization. Failures MUST
be sanitized for clients while retaining non-secret stage and correlation
information for operators.

## Provider Quality Gates

- Labels used for training MUST be explicit human approvals with current evidence
  and verified source/era identity. Drafts and experimental AI labels remain
  distinguishable from approved truth.
- Dataset freezes MUST record inclusion/exclusion reasons, group assignments,
  split identities, label revisions, evidence hashes, and protected-slice hashes.
- Training MUST use only the frozen dataset and declared dependencies. Evaluation
  and calibration MUST be reproducible and isolated from protected fitting.
- A release MUST satisfy pre-registered, per-axis and per-slice acceptance
  thresholds using human-approved labels. Aggregate metrics cannot waive a failed
  protected slice or an unsupported class.
- Inference and feedback MUST validate both JSON Schema and semantic cross-field
  rules. Feedback is append-only pending review and never becomes training truth
  automatically.
- Contract changes MUST be backward-compatible unless an approved migration
  feature defines provider-first rollout, consumer pinning, and rollback.

## Development and Review Workflow

Every model or provider change MUST begin with a Spec Kit feature containing
stable requirements, acceptance scenarios, research, conceptual data, a technical
plan, requirement-quality review, and executable tasks. Tests MUST be written and
observed failing before implementation. Reviews MUST verify this constitution,
the authoritative contract, legacy compatibility, deterministic offline checks,
and the distinction between experimental candidates and approved releases.

Development commands and tests MUST NOT train expensive models, call paid
providers, promote releases, deploy services, apply migrations, or modify
external systems unless a separately approved task explicitly authorizes that
side effect. Review saves and application startup MUST remain side-effect-free
with respect to training and promotion.

## Governance

This constitution supersedes conflicting project practices. Amendments require:

1. a proposed diff with rationale and affected stable requirement identifiers;
2. an impact assessment for datasets, artifacts, releases, consumers, and legacy
   behavior;
3. an explicit maintainer approval and, for breaking changes, a migration and
   rollback plan; and
4. an updated version and amendment date in the same commit.

Constitution versions use semantic versioning. MAJOR removes or incompatibly
changes a principle or ownership boundary; MINOR adds a principle or materially
expands mandatory gates; PATCH clarifies language without changing obligations.
Feature requirements and plans MUST include a Constitution Check before work
begins and again after design. Exceptions are time-bounded, documented with an
owner and expiry, and MUST NOT waive temporal, evidence, group-isolation,
contract-integrity, or explicit-promotion safeguards.

**Version**: 1.0.0 | **Ratified**: 2026-10-06 | **Last Amended**: 2026-10-06

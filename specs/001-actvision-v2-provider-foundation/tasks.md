# Tasks: ActVision v2 Provider Foundation

**Input**: [spec.md](spec.md), [research.md](research.md),
[data-model.md](data-model.md), [contracts/README.md](contracts/README.md), and
[plan.md](plan.md)

**Status**: Future implementation backlog. Every item is intentionally unchecked.
The foundation documents do not claim that model correctness, a full multimodal
release, deployment, migration, or consumer enablement is complete.

**Execution rule**: Each implementation task starts with a focused automated test
that is observed failing for the expected missing behavior, then the smallest
implementation, focused GREEN, and the relevant regression suite. External side
effects require a separately approved execution task.

## Dependencies

```text
Phase 1 Provider Correctness
       |
       +--> Phase 2 Experimental Text/Metadata Candidate (No Release)
       |
       +--> Phase 3 Full Multimodal Release (Future Work)
                    |
                    +--> Phase 4 Release, Inference, and Feedback
                                  |
                                  +--> Phase 5 Deployment and Consumer Shadow
```

Phase 2 may inform design but MUST NOT satisfy or bypass Phase 3/4 release gates.

## Phase 1: Provider Correctness and Contract Foundation

**Goal**: Make evidence, labels, datasets, identities, and compatibility
deterministically testable before new model work.

- [ ] T001 Add canonical evidence-identity tests for exact remarks bytes, structured JSON, selected photo order, known image bytes, exclusions, snapshot time, and changed-input invalidation in `test_actvision_v2.py` (`DATA-002`, `TIME-001`).
- [ ] T002 Add axis-specific label-provenance and UNKNOWN-mask tests covering stale evidence, proposal provenance, retraction, and no cross-axis approval in the v2 dataset tests (`ML-001`, `DATA-003`, `DATA-004`).
- [ ] T003 Add physical-property and evidence-alias split validation tests that reject every train/validation/protected overlap in `test_v2_dataset.py` (`DATA-001`, `ML-002`).
- [ ] T004 Version the authoritative contract bundle with raw-byte schema/fixture hashes and deterministic offline mismatch errors without changing schema semantics (`API-003`, `SEC-002`).
- [ ] T005 Add regression tests proving legacy v1 artifact loading and APIs are unchanged by v2 project/contract validation (`API-003`).

**Phase gate**: Synthetic checks prove identity changes, UNKNOWN masking, group
isolation, fixture semantics, and legacy compatibility offline.

## Phase 2: Experimental Text/Metadata Candidate (No Release)

**Goal**: Evaluate provisional semantic text and structured evidence behavior
without photographs, fusion, release approval, or production claims.

- [ ] T006 Add tests that experimental snapshots admit only declared human labels or explicitly selected draft provenance, mask UNKNOWN, and exclude conflicts/protected groups (`ML-001`, `DATA-001`).
- [ ] T007 Pin and verify the frozen semantic encoder bytes, revision, chunking policy, and local-files-only behavior before fitting supervised text heads (`ML-002`, `SEC-002`).
- [ ] T008 Add acquisition-time structured-feature policy tests that reject arbitrary fields, current/later closes, later resale outcomes, identifiers, and future facts (`TIME-001`, `ML-002`).
- [ ] T009 Report grouped validation agreement, class/group coverage, unsupported classes, missing modalities, and uncalibrated status without labeling draft agreement as human accuracy (`ML-003`, `ML-005`).
- [ ] T010 Keep experimental predictions separate from LabelRevision, Release, canonical MLS fields, and automatic training/promotion paths (`REL-002`, `OPS-003`, `OPS-004`).

**Phase gate**: Results are labeled experimental text/metadata only. No task in
this phase creates a shadow or production release.

## Phase 3: Full Multimodal Release (Future Work)

**Goal**: Build and evaluate the independently identified vision, semantic text,
structured, fusion, and calibration components required by a full v2 release.

- [ ] T011 Resolve OD-001 through OD-005 with reviewed human coverage, pre-register numeric criteria, and record the supported first-release class/mode scope before protected evaluation (`ML-003`, `REL-003`).
- [ ] T012 Implement a point-in-time DatasetVersion freeze that pins eligible label/evidence revisions, groups, splits, exclusions, slice definitions, and policy identity (`DATA-001`, `DATA-003`, `TIME-002`).
- [ ] T013 Compare declared frozen SigLIP2 property-bag aggregations using grouped train/validation only and persist exact backbone and aggregation identities (`ML-002`, `ML-004`).
- [ ] T014 Train supervised semantic-text heads only for adequately covered PRESENT/ABSENT classes, retain exact span provenance, and abstain for unsupported tags (`ML-001`, `ML-004`, `ML-005`).
- [ ] T015 Train allowlisted structured heads from the same frozen dataset and record per-axis supported-class coverage (`ML-002`, `TIME-001`).
- [ ] T016 Generate physical-group out-of-fold component predictions and prove fusion inputs never include in-sample or protected-label outputs (`ML-002`, `ML-004`).
- [ ] T017 Fit candidate fusion and calibration from grouped non-protected data, evaluate explicit missing-modality modes, and retain null confidence where calibration criteria fail (`ML-005`, `API-001`).
- [ ] T018 Execute the pre-registered protected evaluation exactly once per candidate policy and record per-axis, per-class, calibration, abstention, and protected-slice pass/fail with coverage (`ML-003`, `REL-003`).

**Phase gate**: A content-addressed candidate may be assembled only when all
declared artifacts and evaluation records verify. Candidate status is not release
approval.

## Phase 4: Release, Inference, and Feedback

**Goal**: Make a verified candidate consumable only through explicit release and
contract gates.

- [ ] T019 Build a release manifest with explicit vision/text/structured/fusion slots, exact bytes, framework/features/labels/calibration compatibility, dataset, code, evaluation, and approval identities (`REL-001`, `SEC-002`).
- [ ] T020 Add negative release-loader tests for missing approval, failed/absent criteria, path escape, changed bytes, untrusted serialization, and incompatible framework or feature policy (`SEC-002`, `REL-003`).
- [ ] T021 Implement explicit authorized candidate-to-shadow, shadow-to-production, retirement, and rollback transitions with append-only audit history (`REL-002`, `REL-004`).
- [ ] T022 Implement the v2 release adapter and inference modes with request/evidence/release binding, observed photo hashes, explicit component states, and no silent fallback (`API-001`, `API-004`).
- [ ] T023 Complete feedback idempotency, conflict, supersession, workspace, and review-to-new-dataset tests without modifying historical predictions or current freezes (`API-002`, `DATA-003`).
- [ ] T024 Run schema, semantic, bundle, inference, feedback, release, and legacy regression suites entirely offline before any rollout request (`API-003`, `OPS-002`).

**Phase gate**: A shadow-capable provider release exists only after explicit
approval. No canonical MLS product field is writable by this phase.

## Phase 5: Deployment and Consumer Shadow

**Goal**: Validate least-privilege hosted operation and default-off consumer shadow
under separate human approval.

- [ ] T025 Resolve OD-006 through OD-008 and write an approved deployment/retention/rollback feature with capacity budgets and recovery acceptance (`SEC-001`, `OPS-001`).
- [ ] T026 Validate an additive Supabase migration in staging with backup/restore evidence, row/storage restrictions, unchanged evidence fingerprints, and no production change (`SEC-001`, `OPS-002`).
- [ ] T027 Deploy a bounded model-capable hosted worker separately from the lightweight review service; verify workspace scoping, encrypted access, leases, idempotent result recovery, and sanitized diagnostics (`OPS-001`, `OPS-002`).
- [ ] T028 Measure representative shadow latency, throughput, memory, queue bounds, failure recovery, and cost before setting operations limits or enabling traffic (`OPS-001`).
- [ ] T029 Have MLSSourcing pin and validate the exact provider bundle/release, reject mismatches, store results in shadow, and demonstrate that provider credentials cannot write canonical economics, scores, conditions, or ranks (`API-004`, `OPS-004`).
- [ ] T030 Exercise provider-first rollback and consumer disablement while preserving historical predictions, feedback, releases, reviews, and private evidence (`REL-004`, `OPS-004`).

**Phase gate**: Production remains disabled until both repositories separately
approve their release and product policies. Shadow evidence alone does not justify
product ranking behavior.

## Final Convergence

- [ ] T031 Run Spec Kit analysis and resolve contradictions in the originating artifact; do not weaken a constitution or stable requirement to make tasks appear complete.
- [ ] T032 Run all offline provider, contract, legacy, and browser regressions required by the implemented phases and record every skipped external acceptance check explicitly.
- [ ] T033 Run Spec Kit convergence; append concrete missing tasks rather than claiming convergence when any release, deployment, migration, or consumer gate remains open.

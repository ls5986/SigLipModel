# MLS Paired Review Model Iteration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the 21 immutable MLS DEV condition reviews into an explicitly approved, reproducible human challenge set and then a new bounded paired-condition candidate without mutating release `f9bac37b-3ac4-4b64-9b54-cd9581bd226a`.

**Architecture:** Ship three independent changes in order: expose the already-saved remarks/structured evidence in MLS, add an immutable approval and import boundary in Studio, then train and package a new candidate from a separately authorized supplement while retaining a group-isolated human holdout. The current v3 policy remains unchanged until a candidate can be evaluated against enough known TARGET and NOT TARGET human examples.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy, React, TypeScript, Supabase private storage/document state, scikit-learn, pytest, Vitest.

**Spec:** User-approved 21-review audit from 2026-10-12; redacted source extract at `/Users/lindseystevens/.copilot/session-state/a057cc85-4633-40a1-a2bd-80d54fab55f6/files/review-inbox-audit-2026-10-12.json`.

## Global Constraints

- MLS and model-training development environments only; no production reads, writes, deployment, or promotion.
- Preserve raw learned predictions, v3 policy-adjusted decisions, human labels, evidence identities, and prior review events.
- Never mutate the existing release ID, package, manifest, candidate hash, dataset version 5, or `remarks-condition-evidence-v3`.
- Receipt or delivery is not training approval; every imported event requires a separate immutable approval action.
- `INSUFFICIENT_EVIDENCE` is an abstention/evaluation label, not a binary TARGET or NOT TARGET training class.
- Apply corrected photo exclusions before feature extraction; never invent photo-level human labels.
- Use the packaged training-time transformations and pinned encoder identities; do not substitute newer feature extraction.
- Keep physical-property groups together across all splits and prevent listing/photo aliases from crossing train and evaluation.
- Keep the 50-100 TARGET strength as an ordinal research value, never a probability.
- No automatic rescore, broad backfill, retraining, Opportunity change, comp run, or production release.
- Create a new `ai:release` issue before packaging or promoting a new candidate.

## Review Focus

- Multiple events for one prediction or listing must be shown as history and never counted as independent examples.
- A prior prediction for the same listing must not silently replace the current evidence snapshot.
- Missing photo bytes and reviewer photo exclusions must make image-axis eligibility explicit.
- Missing extracted phrases must not be presented as if remarks were absent or unscored.
- The human sample's TARGET/NOT TARGET imbalance must not leak all scarce TARGET examples into training.

---

### Task 1: Expose Saved Remarks and Structured Decisions in MLS

**Files:**
- Modify: `MLSsourcing/backend/app/schemas/paired_condition_reviews.py`
- Modify: `MLSsourcing/backend/app/services/acquisition/paired_condition_reviews.py`
- Modify: `MLSsourcing/frontend/src/deluxe/pages/PairedConditionReviews.tsx`
- Test: `MLSsourcing/backend/tests/test_paired_condition_reviews.py`
- Test: `MLSsourcing/frontend/src/deluxe/pages/PairedConditionReviews.test.tsx`

**Interfaces:**
- Consumes: saved `ActVisionPrediction.result.learned_prediction`, final `remarks` and `structured` decisions, and `evidence_policy.signals`.
- Produces: `learned_remarks`, `learned_structured`, `final_remarks`, `final_structured`, and typed source-attributed `policy_signals` on `PairedReviewDetailRead`.

- [ ] **Step 1: Write failing backend tests**

Assert that a detail response returns learned and final remarks/structured decisions plus public/private policy signals while preserving the existing public remarks and sanitized private snippets.

- [ ] **Step 2: Run the focused backend test and verify it fails**

Run: `pytest backend/tests/test_paired_condition_reviews.py -q`

Expected: FAIL because the new detail fields do not exist.

- [ ] **Step 3: Implement the read-only detail fields**

Add typed schema models for policy signals and populate them directly from the immutable prediction result. Do not rerun extraction or change any prediction.

- [ ] **Step 4: Write failing frontend tests**

Assert that technical details display raw remarks and structured decisions, saved condition phrases show their true `PublicRemarks` or `PrivateRemarks` source, and an empty signal list says that no policy phrases were extracted while still showing that remarks were scored.

- [ ] **Step 5: Run the focused frontend test and verify it fails**

Run: `cd frontend && npm test -- PairedConditionReviews.test.tsx`

Expected: FAIL on the new evidence and source labels.

- [ ] **Step 6: Implement the reviewer diagnostics**

Render the new fields inside the existing collapsed technical section and source-attributed phrase panel. Preserve the three human assessment axes and all save behavior.

- [ ] **Step 7: Verify Task 1**

Run:

```bash
pytest backend/tests/test_paired_condition_reviews.py -q
cd frontend && npm test -- PairedConditionReviews.test.tsx
```

Expected: both commands pass.

- [ ] **Step 8: Commit the MLS-only change**

Create a focused MLS topic commit after the existing navigation slice is resolved; do not mix it into the uncommitted navigation work.

---

### Task 2: Add an Immutable Paired Feedback Approval Inbox

**Files:**
- Create: `SigLipModel/paired_feedback_inbox.py`
- Modify: `SigLipModel/cloud_runtime.py`
- Modify: `SigLipModel/hosted_server.py`
- Modify: `SigLipModel/hosted_status.html`
- Test: `SigLipModel/test_paired_feedback_inbox.py`
- Test: `SigLipModel/test_hosted_runtime.py`

**Interfaces:**
- Consumes: immutable documents keyed `paired-condition-feedback-inbox:<event_id>`.
- Produces: `list_feedback(store, release_id) -> list[FeedbackReview]`, `approve_feedback(store, event_id, event_sha256, actor, usage) -> Approval`, and immutable documents keyed `paired-condition-feedback-approval:<event_id>`.

- [ ] **Step 1: Write failing inbox normalization tests**

Cover the current 24 events: two same-prediction duplicate review histories, one prior prediction for a listing, and 21 latest listing reviews. Assert that history remains visible and no event is silently approved or counted twice.

- [ ] **Step 2: Run the inbox tests and verify they fail**

Run: `pytest -q test_paired_feedback_inbox.py`

Expected: FAIL because the inbox module does not exist.

- [ ] **Step 3: Implement read-only inbox normalization**

Validate each event and its stored digest, group by listing/evidence/prediction identity, and mark current, prior, and repeated-review relationships without deleting any event.

- [ ] **Step 4: Write failing approval tests**

Assert that approval requires the event digest, reviewer identity, explicit usage (`challenge` or `training_candidate`), and an axis eligibility decision. Reject missing exclusion reasons, mismatched release identities, unknown evidence, and `INSUFFICIENT_EVIDENCE` marked as binary training truth.

- [ ] **Step 5: Implement immutable approvals**

Write a new approval document at revision zero. Never mutate the received inbox document. A second identical request is idempotent; conflicting reuse fails.

- [ ] **Step 6: Add the Studio inbox UI and API**

Expose pending, held, and approved statuses separately from the existing Studio Corrections batch. Show the saved model/human axes, evidence identity, missing modalities, notes, quotes, and corrected-photo references.

- [ ] **Step 7: Verify Task 2**

Run:

```bash
pytest -q test_paired_feedback_inbox.py test_hosted_runtime.py
npm test
```

Expected: all focused Python and existing browser-contract tests pass.

- [ ] **Step 8: Commit the Studio approval boundary**

Commit without approving any real event or starting training.

---

### Task 3: Freeze a Human Feedback Challenge Supplement

**Files:**
- Create: `SigLipModel/paired_feedback_dataset.py`
- Modify: `SigLipModel/paired_condition.py`
- Modify: `SigLipModel/paired_condition_models.py`
- Test: `SigLipModel/test_paired_feedback_dataset.py`
- Test: `SigLipModel/test_paired_condition_models.py`

**Interfaces:**
- Consumes: approved feedback documents from Task 2 and the exact v5 candidate transformations.
- Produces: `freeze_feedback_supplement(store, release_id, actor) -> SupplementManifest` and `materialize_feedback_rows(store, manifest) -> list[TrainingRow]`.

- [ ] **Step 1: Write failing supplement provenance tests**

Assert that the manifest includes each event ID/hash, release identity, evidence ID, physical-property group, listing key, photo hashes, corrected exclusions, label eligibility, transformation version, encoder identities, and a deterministic supplement SHA256.

- [ ] **Step 2: Run the supplement tests and verify they fail**

Run: `pytest -q test_paired_feedback_dataset.py`

Expected: FAIL because the supplement module does not exist.

- [ ] **Step 3: Implement deterministic supplement freezing**

Default every newly approved event to `challenge`. Require a separate approval revision before `training_candidate` use. Preserve all source events and write a new immutable supplement document instead of changing dataset version 5.

- [ ] **Step 4: Write failing feature-parity tests**

Assert that public/private remarks use the candidate's `sanitize_text`, structured fields use `structured_features`, excluded photos are removed before `vision_features`, cached embeddings require matching content hash and encoder identity, and no model training occurs during materialization.

- [ ] **Step 5: Implement feature materialization**

Reuse the packaged classifier source and encoder manifests. Refuse missing or mismatched encoder identities rather than substituting a newer transformation.

- [ ] **Step 6: Add group/leakage tests**

Assert that physical-property aliases, listing aliases, and repeated photo hashes stay in one effective split when base v5 rows and supplement rows are combined.

- [ ] **Step 7: Verify Task 3**

Run: `pytest -q test_paired_feedback_dataset.py test_paired_condition_models.py`

Expected: all supplement, parity, and leakage tests pass.

- [ ] **Step 8: Commit the immutable supplement adapter**

Commit without changing dataset version 5 or training a candidate.

---

### Task 4: Evaluate Baseline and Gate a New Candidate

**Files:**
- Create: `SigLipModel/paired_human_evaluation.py`
- Modify: `SigLipModel/paired_condition.py`
- Test: `SigLipModel/test_paired_human_evaluation.py`

**Interfaces:**
- Consumes: Task 3 supplement rows, candidate bundle `f9bac37b-3ac4-4b64-9b54-cd9581bd226a`, and any proposed new bundle.
- Produces: `evaluate_human_challenge(bundle, rows, policy) -> HumanEvaluationReport`.

- [ ] **Step 1: Write failing metric tests**

Report each axis separately: known human labels, exact agreements, opposite-class errors, abstentions on known labels, correct abstentions, class-specific recall, policy transitions, photo coverage, and modality failures. Never report strength as probability.

- [ ] **Step 2: Pin the audited baseline**

Assert these current-release results for the 21 latest reviews:

- image known: 17; exact: 12; abstained: 2; opposite: 3;
- metadata known: 18; exact: 17; abstained: 0; opposite: 1;
- learned overall known: 17; exact: 10; abstained: 2; opposite: 5;
- v3 final overall known: 17; exact: 9; abstained: 8; opposite: 0;
- v3 changes eight learned TARGET results to `INSUFFICIENT_EVIDENCE`.

- [ ] **Step 3: Run the evaluation tests and verify they fail**

Run: `pytest -q test_paired_human_evaluation.py`

Expected: FAIL because the evaluator does not exist.

- [ ] **Step 4: Implement the human challenge evaluator**

Keep binary errors and abstentions separate. Report TARGET and NOT TARGET support so the current two known human overall TARGET examples cannot be mistaken for a reliable promotion set.

- [ ] **Step 5: Add a promotion gate**

Block human-accuracy promotion claims until the held-out challenge contains at least 10 independent known TARGET and 10 independent known NOT TARGET physical-property groups for the promoted axis. Keep all current events in `challenge` until the user explicitly authorizes a training split.

- [ ] **Step 6: Verify Task 4**

Run: `pytest -q test_paired_human_evaluation.py`

Expected: the pinned baseline passes and the current sample fails only the documented class-support promotion gate.

- [ ] **Step 7: Commit the evaluation harness**

Commit without training or modifying v3.

---

### Task 5: Train and Package the Next Immutable Candidate

**Files:**
- Modify: `SigLipModel/paired_condition.py`
- Modify: `SigLipModel/paired_condition_models.py`
- Modify: `SigLipModel/paired_dev_package.py`
- Test: `SigLipModel/test_paired_condition_models.py`
- Test: `SigLipModel/test_paired_dev_package.py`

**Interfaces:**
- Consumes: frozen v5 base dataset, an explicitly authorized training supplement from Task 3, and the held-out challenge evaluator from Task 4.
- Produces: a new candidate ID, candidate SHA256, manifest SHA256, immutable policy identity, and rollback identity.

- [ ] **Step 1: Create the `ai:release` issue**

Record the baseline release, supplement hash, proposed training use, held-out challenge identities, encoder identities, transformation versions, intended metrics, latency/cost bounds, and rollback candidate.

- [ ] **Step 2: Write failing candidate-composition tests**

Assert that only `training_candidate` approvals join the base training rows, challenge rows never enter fitting, known labels train their matching axes only, and `INSUFFICIENT_EVIDENCE` remains excluded from binary fitting.

- [ ] **Step 3: Implement bounded candidate composition**

Keep the existing group-aware splitting and OOF fusion. Do not replace the training-time text or structured transformations.

- [ ] **Step 4: Train one candidate explicitly**

Run the existing explicit training action with the new immutable supplement identity. Do not run a market rescore or backfill.

- [ ] **Step 5: Evaluate before policy changes**

Compare the new raw model against the frozen v5 baseline on the original validation/test splits and the human challenge. Prioritize the image false-positive pattern; retain v3 unchanged for the first comparison.

- [ ] **Step 6: Consider a v4 policy only if separately justified**

If raw-model improvement is insufficient, create `remarks-condition-evidence-v4` as a new identity and evaluate it independently. It must not increase opposite-class overall errors and must report any additional abstentions.

- [ ] **Step 7: Package the development candidate**

Create a new release ID and immutable package with candidate, manifest, supplement, encoder, policy, code-commit, and evaluation hashes. Keep the prior release available for rollback and mark the new release development-only.

- [ ] **Step 8: Verify the package**

Run:

```bash
pytest -q test_paired_condition_models.py test_paired_dev_package.py test_paired_human_evaluation.py
```

Expected: package integrity, feature parity, original split evaluation, human challenge evaluation, and rollback identity all pass.

- [ ] **Step 9: Commit the candidate code and manifest**

Commit the reproducible code/manifest only. Do not commit credentials, private review payloads, photos, or populated environment files.

---

### Task 6: Promote Only to MLS Development

**Files:**
- Modify: MLS development environment configuration through its authorized secret/config channel.
- Test: existing MLS paired-condition contract and worker tests.

**Interfaces:**
- Consumes: the verified Task 5 development release ID and hashes.
- Produces: a bounded MLS DEV canary with the old release retained as rollback.

- [ ] **Step 1: Update the pinned development release**

Change only MLS DEV/model-worker development configuration. Do not mutate the old release or production configuration.

- [ ] **Step 2: Score a bounded three-record canary**

Use one clearly dated, one clearly updated, and one mixed/insufficient property. Reuse evidence only when content and encoder identities match.

- [ ] **Step 3: Verify development**

Check `/health`, `/ready`, `/release`, worker status, the three real property journeys, raw/final decision separation, and absence of automatic backfill.

- [ ] **Step 4: Expand only after the canary passes**

Run a bounded independent batch, compare candidate and rollback release, and report record-level successes and failures.

- [ ] **Step 5: Stop for human promotion review**

Do not create a production release until the user accepts the development evidence.


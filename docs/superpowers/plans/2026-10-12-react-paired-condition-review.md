# React Paired-Condition Review Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace draft PR #34's legacy review wiring with a React condition-review slice that uses the existing paired-condition development contract and preserves model, evidence, and human-review provenance.

**Architecture:** Build the React UI on an integration branch descended from `feature/paired-condition-mls-dev-integration-20261011`, because those APIs are not present on `develop`. Keep the Python same-origin service responsible for authentication, reviewer identity, revisions, and writes; the browser sends only evidence-bound correction data. Supersede or retarget draft PR #34 rather than merging its current legacy-API implementation.

**Tech Stack:** Python 3.12, React 19, TypeScript, Vite, Playwright, Python `unittest`/pytest-compatible tests

**Spec:** `docs/react-condition-review-intake.md`

## Global Constraints

- Keep `/review` or the current server-selected Studio page available as a rollback until real DEV acceptance passes.
- Do not merge into `develop` while the PR is draft, required checks are absent, or real DEV acceptance is failing.
- Do not add, alter, or apply a database migration for this slice.
- Do not start intake, recovery, automatic labeling, training, rescoring, or promotion jobs.
- Do not mutate frozen datasets, existing predictions, or earlier human reviews.
- Bind reviewer identity on the server; never accept a browser-supplied reviewer name.
- Keep current-candidate predictions, older automated-silver tags, and human reviews visibly separate.
- A human review remains attached to its saved evidence and candidate; never silently copy it to the current candidate.
- Property Photos is a learned aggregate and must not be described as a vote over photo probes.
- Search in this slice filters the saved review queue only; do not present it as provider MLS/address intake.

## Review Focus

- A human review for an earlier candidate must display an explicit stale-candidate warning and remain unchanged.
- Missing photos must require Photos = Cannot tell while leaving Metadata independently reviewable.
- Multiple photo probes from one room must not be counted or described as multiple dated areas.
- A failed, timed-out, or non-JSON API response must show an actionable error and must not look like an empty queue.
- Concurrent review revisions must reject the save, retain the user's form values, and require reload before retry.

---

### Task 1: Establish the Integration Branch

**Files:**
- Modify: Git branch topology only
- Reference: `docs/react-condition-review-intake.md`

**Interfaces:**
- Consumes: `feature/paired-condition-mls-dev-integration-20261011` at `74b06e576244a392d6b517e6cfdf0a07335543ac`; PR #34 commit `67666e4a32fe456e5c010262438ae5b0315298bb`
- Produces: `integration/react-paired-condition-review` with the paired backend followed by the React frontend commit

- [ ] **Step 1: Verify both source commits and preserve dirty worktrees**

Run:

```bash
git cat-file -e 74b06e576244a392d6b517e6cfdf0a07335543ac^{commit}
git cat-file -e 67666e4a32fe456e5c010262438ae5b0315298bb^{commit}
git worktree list --porcelain
```

Expected: both commits exist; the dirty `feature/corrected-prior-last-review` worktree is listed and untouched.

- [ ] **Step 2: Create an isolated integration worktree from the paired backend**

Run:

```bash
git worktree add <session-worktree-path> -b integration/react-paired-condition-review 74b06e576244a392d6b517e6cfdf0a07335543ac
```

Expected: a clean worktree on `integration/react-paired-condition-review`.

- [ ] **Step 3: Cherry-pick the PR #34 frontend commit**

Run:

```bash
git cherry-pick 67666e4a32fe456e5c010262438ae5b0315298bb
```

Expected: the React files are added; conflicts in `hosted_server.py`, `render.yaml`, or tests are resolved in favor of paired-condition routes plus the React asset-serving behavior.

- [ ] **Step 4: Verify the integration baseline**

Run:

```bash
git diff --check 74b06e576244a392d6b517e6cfdf0a07335543ac..HEAD
```

Expected: exit code 0.

- [ ] **Step 5: Commit conflict resolutions if cherry-pick required them**

```bash
git add hosted_server.py render.yaml test_hosted_server.py frontend package.json package-lock.json
git commit -m "chore: integrate React review with paired backend"
```

### Task 2: Define the Paired-Condition Frontend Contract

**Files:**
- Modify: `frontend/src/review/api.ts`
- Modify: `frontend/tests/evidence-review.spec.ts`

**Interfaces:**
- Consumes: `GET /api/studio/paired-condition/status`, `GET /api/studio/paired-condition/list`, `GET /api/studio/paired-condition/property?id=<group_id>`, `POST /api/studio/paired-condition/correct`
- Produces: `loadStatus(): Promise<PairedStatus>`, `loadQueue(): Promise<PairedQueue>`, `loadProperty(groupId: string): Promise<PairedProperty>`, `saveCorrection(payload: CorrectionRequest): Promise<CorrectionResponse>`, and typed provenance fields for `candidate`, `prediction`, `photo_tags`, `silver_reference`, `review`, and `revision`

- [ ] **Step 1: Write the failing browser contract test**

Update `frontend/tests/evidence-review.spec.ts` so its mocked URLs are the four paired-condition endpoints. Assert that opening a property renders the current candidate ID, labels `prediction.photos[]` as current per-photo model probes, labels `photo_tags[]` as the older automated-silver system, and sends no `reviewer` field in the correction request.

- [ ] **Step 2: Run the test to verify it fails**

Run:

```bash
npm run test:e2e -- --grep "paired-condition provenance"
```

Expected: FAIL because the client still calls `review-queue`, `property`, and `review`.

- [ ] **Step 3: Replace the legacy API types and functions**

In `frontend/src/review/api.ts`, define exact paired-condition response types from `paired_condition.py::detail`, `paired_condition_photo_queue.py::photo_queue`, and `paired_condition.py::correction`. Keep token capture and login redirection in one request helper. Define `mediaUrl(evidenceId: string, sha256: string, thumbnail?: boolean): string` against the paired-review image endpoint used by saved evidence.

- [ ] **Step 4: Run TypeScript compilation**

Run:

```bash
npm run build
```

Expected: Vite build succeeds with no TypeScript errors.

- [ ] **Step 5: Commit**

```bash
git add frontend/src/review/api.ts frontend/tests/evidence-review.spec.ts
git commit -m "refactor: use paired-condition review contract"
```

### Task 3: Render Evidence and Provenance Separately

**Files:**
- Modify: `frontend/src/review/EvidenceReview.tsx`
- Modify: `frontend/src/style.css`
- Modify: `frontend/tests/evidence-review.spec.ts`

**Interfaces:**
- Consumes: `PairedQueue`, `PairedProperty`, and media URLs from Task 2
- Produces: queue/detail UI with separately labeled saved photos, current property prediction, current per-photo probes, older automated-silver tags, listing-source evidence, and saved human review

- [ ] **Step 1: Add failing provenance and stale-review assertions**

Add a fixture where `candidate.candidate_id` differs from the candidate ID stored in the latest human review answer. Assert that the page displays both IDs and the text `Earlier prediction review — not transferred`. Add a second fixture where they match and assert that warning is absent.

- [ ] **Step 2: Add failing property-aggregation assertions**

Assert that the property Photos panel says `Learned property image head` and `Not a vote over photo probes`. Assert that duplicate kitchen probes remain individual photo rows and that the UI does not summarize them as a count of dated rooms.

- [ ] **Step 3: Run the focused browser tests**

Run:

```bash
npm run test:e2e -- --grep "provenance|property image head"
```

Expected: FAIL on the missing labels and stale-candidate warning.

- [ ] **Step 4: Implement the queue and evidence view**

Refactor `EvidenceReview.tsx` to load paired queue/property data, keep search as local saved-queue filtering, show acquisition/resale events independently, and render the provenance panels required by the tests. Do not infer equivalence between `prediction.photos[]` and `photo_tags[]`.

- [ ] **Step 5: Run the focused browser tests**

Run:

```bash
npm run test:e2e -- --grep "provenance|property image head"
```

Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add frontend/src/review/EvidenceReview.tsx frontend/src/style.css frontend/tests/evidence-review.spec.ts
git commit -m "feat: show paired review provenance"
```

### Task 4: Implement Independent T/NT/CT Review Saving

**Files:**
- Modify: `frontend/src/review/EvidenceReview.tsx`
- Modify: `frontend/tests/evidence-review.spec.ts`
- Modify: `test_paired_condition_evidence.py`

**Interfaces:**
- Consumes: `CorrectionRequest` and `saveCorrection()` from Task 2
- Produces: independent Photos, Metadata, and Overall decisions; TARGET-only integer strength from 50 through 100 for Photos and Metadata; evidence-bound `candidate_id`, `evidence_id`, `expected_revision`, notes, and exclusions

- [ ] **Step 1: Write failing UI save tests**

Assert that Photos, Metadata, and Overall each accept `TARGET`, `NOT_TARGET`, or `INSUFFICIENT_EVIDENCE`; only TARGET shows strength; Save and Save → Next send the current evidence ID, candidate ID, expected revision, and exclusions; the payload contains no reviewer; direct Photos/Metadata disagreement does not auto-select Overall.

- [ ] **Step 2: Write failing edge-case tests**

Assert that a listing with no usable photos requires Photos = `INSUFFICIENT_EVIDENCE`; a 409/revision-style save failure retains entered values; and a non-JSON or 502 response renders an error rather than an empty queue.

- [ ] **Step 3: Run the focused tests**

Run:

```bash
npm run test:e2e -- --grep "independent review|save and next|revision conflict|API failure"
```

Expected: FAIL because the current UI uses one 1–5 target-fit score.

- [ ] **Step 4: Implement the review form**

Replace target-fit scoring in `EvidenceReview.tsx` with independent T/NT/CT controls. Use the backend's existing correction rules; do not add scope to the stored contract in this slice. Allow notes to describe localized evidence without treating notes as a model feature.

- [ ] **Step 5: Pin server-side actor provenance**

In `test_paired_condition_evidence.py`, add or extend a correction test to assert that `paired_condition.correction(store, payload, actor)` persists `actor` as reviewer and ignores no browser reviewer because the field is rejected by the allowed-field set.

- [ ] **Step 6: Run frontend and backend focused tests**

Run:

```bash
npm run test:e2e -- --grep "independent review|save and next|revision conflict|API failure"
python3.12 -m pytest test_paired_condition_evidence.py -q
```

Expected: all selected tests pass.

- [ ] **Step 7: Commit**

```bash
git add frontend/src/review/EvidenceReview.tsx frontend/tests/evidence-review.spec.ts test_paired_condition_evidence.py
git commit -m "feat: add independent paired corrections"
```

### Task 5: Gate the Route and Verify the Integrated Slice

**Files:**
- Modify: `hosted_server.py`
- Modify: `test_hosted_server.py`
- Modify: `docs/react-condition-review-intake.md`

**Interfaces:**
- Consumes: built `frontend-dist`, paired-condition routes, existing hosted authentication
- Produces: authenticated `/property-review` React route with explicit rollback, no change to production routes, and documented acceptance status

- [ ] **Step 1: Write failing hosted-route tests**

Add tests asserting `/property-review` requires authentication, serves the built React index only when `frontend-dist` exists, confines `/assets/*`, and leaves `/review` or `/studio` behavior unchanged. Assert that paired-condition POST routes derive the actor from authenticated server state.

- [ ] **Step 2: Run the hosted tests**

Run:

```bash
python3.12 -m pytest test_hosted_server.py -q
```

Expected: FAIL on any missing route or actor assertion.

- [ ] **Step 3: Implement the guarded route**

Update `hosted_server.py` only as needed to serve the React build without replacing the existing review route. Keep the paired-condition route implementation and server-side actor binding from the backend branch.

- [ ] **Step 4: Run all local gates**

Run:

```bash
npm ci
npm run build
npm run test:e2e
python3.12 -m pytest test_hosted_server.py test_paired_condition_service.py test_paired_condition_evidence.py test_paired_condition_api.py test_paired_condition_models.py test_paired_condition_batches.py -q
git diff --check origin/develop...HEAD
```

Expected: all commands exit 0.

- [ ] **Step 5: Perform real DEV read-only acceptance**

Using the authenticated development deployment, verify the real queue, a property with photos, a property without usable photos, current/older prediction provenance, an earlier-candidate human review warning, and page reload. Do not save until the DEV deployment and database are confirmed isolated from production/shared training data.

Expected: all reads succeed without 5xx responses and match the saved evidence.

- [ ] **Step 6: Perform one isolated DEV save/reload/Save → Next acceptance**

Save a disposable DEV-only review, reload it, then use Save → Next. Confirm the stored reviewer is the authenticated server actor, the candidate/evidence IDs remain attached, revision increments, and no dataset/training state changes.

Expected: all acceptance checks pass; if no isolated writable DEV database exists, leave the PR draft and record this gate as blocked.

- [ ] **Step 7: Update implementation status**

In `docs/react-condition-review-intake.md`, record exactly which local and real DEV checks passed. State that staged intake, training eligibility, and comparables remain separate follow-up work.

- [ ] **Step 8: Commit**

```bash
git add hosted_server.py test_hosted_server.py docs/react-condition-review-intake.md
git commit -m "test: gate React paired review rollout"
```

- [ ] **Step 9: Publish without merging**

Push `integration/react-paired-condition-review`, open a replacement draft PR to `develop`, and link #34 as superseded. Mark ready and merge only after CI, review, and every real DEV acceptance gate above passes.

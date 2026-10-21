# ActVision v2 Development Load Smoke Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Prove that the exact existing ActVision v2 candidate, its frozen encoders, calibration objects, and four artifacts can load together in an offline development process without inference or remote writes.

**Architecture:** Add a fail-closed local CLI that consumes an operator-prepared bundle and original encoder snapshots. It validates immutable identities before deserializing artifacts, loads every model without calling prediction/embedding methods, emits a JSON report to stdout, and has no database, storage-write, queue, worker, or deployment path.

**Tech Stack:** Python 3.12.14, scikit-learn 1.6.1, joblib 1.4.2, CPU Torch 2.6.0, Transformers 4.57.6, sentence-transformers 5.1.2, pytest.

**Spec:** User request dated 2026-10-14 and verified release manifest for `f134590b-122b-42dc-9a27-209523e2a33c`; no separate design document.

## Global Constraints

- Candidate release: `f134590b-122b-42dc-9a27-209523e2a33c`.
- Canonical bundle SHA-256: `7527a12908fc62b6a94f86d3337ff5a8ccd25c6d7c50e291a016f5bf4d8dee00`.
- Artifact SHA-256 values:
  - Vision: `95e4f26874850ebd65a90ab17b2731c9f5ec982086e67bf3262eff0864d92c01`.
  - Text: `79224f3d1037a2df286f14fcc80d40dc7f2a53b77e0b317518c207cf46dc933a`.
  - Structured: `21fcac42135029d81941ea05fa12de395ebce9cf1ecc0407472fee31318fa85c`.
  - Fusion: `fa87a668c06b4e3251f3499d3ac9a4a72a080cba9f9aa5cb9c86dc09472d0ae5`.
- The original SigLIP2 revision must be recovered from the original worker cache/provenance record. Do not query or substitute the current Hugging Face HEAD.
- Text encoder must remain `sentence-transformers/all-MiniLM-L6-v2` revision `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`, checkpoint SHA-256 `dfdac870e42d2700ab4813bb3ad1dab8c9f992d4a99d5f50e4f5732610d30d92`.
- The smoke command must set Hugging Face and Transformers offline modes and use only local files.
- The smoke command must not call `predict`, `predict_proba`, `transform`, `encode`, `embed`, `classify`, or a model forward pass.
- Do not connect to Supabase, enqueue work, start a worker, retrain, update a release, deploy, or write remote state.
- A missing original SigLIP2 revision is a blocking failure, not permission to use a newer revision.

## Review Focus

- Original vision provenance is missing or cannot be traced to the v2 worker: fail before model loading.
- One artifact has valid joblib bytes but the wrong hash, schema, or calibration version: fail before reporting success.
- Installed package versions differ from the release runtime: fail with the exact dependency mismatch.
- A supposedly load-only path invokes any prediction or embedding operation: fail the test.
- The CLI inherits Supabase credentials or attempts network access: run without loading application environment files and fail any network attempt.

---

### Task 1: Define the fail-closed load-smoke contract

**Files:**
- Create: `actvision_load_smoke.py`
- Test: `test_actvision_load_smoke.py`

**Interfaces:**
- Consumes: a local release manifest, local artifact root, original `backbone.json` plus its local SigLIP2 snapshot, and the pinned local semantic-text snapshot.
- Produces:
  - `SmokeExpectations` dataclass containing the exact release, bundle, component, framework, encoder, and calibration identities.
  - `verify_inputs(paths: SmokePaths, expected: SmokeExpectations) -> VerifiedSmokeInputs`.
  - `load_only(inputs: VerifiedSmokeInputs) -> dict`.

- [ ] **Step 1: Write tests that pin all exact release identities**

Test `test_verify_inputs_accepts_exact_v2_candidate` with the release ID, bundle SHA, four artifact hashes, four feature schemas, four calibration versions, scikit-learn `1.6.1`, and the pinned text encoder identity.

- [ ] **Step 2: Write fail-closed provenance tests**

Add parameterized tests proving that a changed release ID, bundle hash, artifact hash, calibration version, feature schema, framework version, text revision, text checkpoint hash, vision model ID, or vision revision raises a specific `ValueError`.

- [ ] **Step 3: Write a test proving the missing vision revision blocks the smoke**

`test_missing_original_siglip_revision_blocks_before_deserialization` must assert that no `joblib.load` call occurs.

- [ ] **Step 4: Run the focused tests and confirm they fail**

Run: `pytest -q test_actvision_load_smoke.py`

Expected: FAIL because `actvision_load_smoke` does not exist.

- [ ] **Step 5: Implement identity and file verification**

Use the existing release contract and canonical JSON digest. Verify every artifact byte hash before deserialization. Verify the text checkpoint with `provision_semantic_encoder.verify`. Verify the original SigLIP2 snapshot against the recovered `backbone.json`, including model ID, exact revision, and recorded weight hashes.

- [ ] **Step 6: Run the focused tests**

Run: `pytest -q test_actvision_load_smoke.py`

Expected: PASS.

- [ ] **Step 7: Commit**

```bash
git add actvision_load_smoke.py test_actvision_load_smoke.py
git commit -m "test: add fail-closed ActVision load preflight"
```

### Task 2: Load the four artifacts and both encoders without inference

**Files:**
- Modify: `actvision_load_smoke.py`
- Modify: `test_actvision_load_smoke.py`

**Interfaces:**
- Consumes: `VerifiedSmokeInputs` from Task 1.
- Produces: a JSON-serializable report with release identity, dependency versions, encoder identities, component classes, schemas, calibration versions, artifact hashes, elapsed time, peak resident memory, and `inference_executed: false`.

- [ ] **Step 1: Write tests for artifact compatibility**

Create inert fixture artifacts with the same wrapper shape as the production `CalibratedModel` objects. Assert that all four deserialize, expose the manifest feature schema, and expose the exact manifest calibration version.

- [ ] **Step 2: Write a no-inference guard test**

Patch prediction, transformation, encoding, embedding, classification, and forward methods to raise. Assert `load_only` succeeds without invoking any patched method.

- [ ] **Step 3: Write dependency and model-class rejection tests**

Assert explicit failures for Python/scikit-learn/joblib/Torch/Transformers/sentence-transformers mismatches and for an artifact whose loaded class or nested calibrator is incompatible.

- [ ] **Step 4: Run tests and confirm the new cases fail**

Run: `pytest -q test_actvision_load_smoke.py`

Expected: FAIL at the unimplemented load-only checks.

- [ ] **Step 5: Implement offline model loading**

Set `HF_HUB_OFFLINE=1` and `TRANSFORMERS_OFFLINE=1` before importing encoder libraries. Load the SigLIP2 processor/model from the verified original snapshot with `local_files_only=True`; load the sentence-transformer checkpoint locally without calling `encode`; deserialize each hash-verified artifact; validate feature schema and nested calibration identity; never construct a store or application runtime.

- [ ] **Step 6: Emit resource measurements without writing a report file**

Return elapsed wall time and peak RSS in the report. The caller prints JSON to stdout.

- [ ] **Step 7: Run focused and adjacent tests**

Run: `pytest -q test_actvision_load_smoke.py test_actvision_v2.py`

Expected: PASS.

- [ ] **Step 8: Commit**

```bash
git add actvision_load_smoke.py test_actvision_load_smoke.py
git commit -m "feat: validate ActVision candidate model loading"
```

### Task 3: Add the explicit local smoke command and runbook

**Files:**
- Create: `tools/smoke_actvision_v2_load.py`
- Create: `docs/actvision-v2-load-smoke.md`
- Modify: `test_actvision_load_smoke.py`

**Interfaces:**
- Consumes: `python tools/smoke_actvision_v2_load.py --bundle-root PATH --vision-provenance PATH --text-checkpoint PATH`.
- Produces: JSON on stdout and exit status `0` only when every identity and load check passes.

- [ ] **Step 1: Write CLI tests**

Assert that the CLI requires all three local paths, refuses any release other than the exact candidate, does not read `.env` files, has no database/storage-write imports, and prints `inference_executed: false` on success.

- [ ] **Step 2: Run tests and confirm the CLI cases fail**

Run: `pytest -q test_actvision_load_smoke.py`

Expected: FAIL because the CLI does not exist.

- [ ] **Step 3: Implement the thin CLI**

The CLI must only parse paths, call Task 1 and Task 2 interfaces, print the report, and return nonzero on a verification or load error.

- [ ] **Step 4: Document bundle preparation and stop conditions**

Document that artifact retrieval is a separate read-only operator action, the local bundle mirrors the manifest URIs, the original vision provenance must be recovered first, and no command should be run if it cannot be recovered.

- [ ] **Step 5: Run the full targeted verification**

Run: `pytest -q test_actvision_load_smoke.py test_actvision_v2.py test_v2_training_io.py`

Expected: PASS.

- [ ] **Step 6: Run the real load smoke only after implementation approval and provenance recovery**

Run in an isolated Python 3.12.14 environment with no application `.env` loaded:

```bash
python tools/smoke_actvision_v2_load.py \
  --bundle-root /absolute/path/to/read-only-v2-bundle \
  --vision-provenance /absolute/path/to/original/backbone.json \
  --text-checkpoint /absolute/path/to/pinned/text/checkpoint
```

Expected: exit `0`, all exact identities in stdout, four components loaded, both encoders loaded locally, and `inference_executed: false`.

- [ ] **Step 7: Commit**

```bash
git add tools/smoke_actvision_v2_load.py docs/actvision-v2-load-smoke.md test_actvision_load_smoke.py
git commit -m "docs: add ActVision v2 load smoke command"
```

## Separately Approved Follow-up: 3–5 Property Inference

This is not part of the load-smoke implementation.

After a passing smoke, request separate inference approval to run one local, non-persistent process against exact acquisition snapshots for:

- `1120072525` — 17788 Caminito Balata.
- `1151195349` — 6739 Cantil St.
- `1147462608` — 5703 Michael St.
- `1150789139` — 17523 Plaza Marlena.
- `1156423432` — 2767 Vista Del Oro.

Before inference, produce a read-only evidence manifest for each property containing the acquisition listing key, evidence cutoff, snapshot hash, selected acquisition-photo hashes, exact public/private remarks supplied, structured fields supplied, and explicit missing modalities. Reject any photo not bound to the acquisition listing and cutoff. Never include resale imagery, resale remarks, resale price, or future outcomes.

The first inference run should remain local and write only a session report. It should not enqueue work or persist predictions to Supabase. Display Vision, Text, Structured, and Fusion results, component/calibration versions, evidence consumed, and missing evidence. UI correction and database persistence require another explicit approval.

## Corrected Acquisition-Fit Experiment

- Preserve every existing frozen train/validation/test membership.
- Use at least **24 independent existing TRAIN groups**: 12 approved acquisition-fit TARGET and 12 approved acquisition-fit NOT_TARGET. Twenty is the hard trainer minimum; 24 reduces the chance that a grouped fold lacks a class.
- Review at least **8 existing VALIDATION groups**: 4 TARGET and 4 NOT_TARGET, without moving groups between splits.
- Evaluate on the **entire pre-existing protected test split** or a label-blind, pre-registered subset selected before viewing outcomes. Do not choose protected examples because they are TARGET, NOT_TARGET, profitable, or easy.
- Sufficient acquisition-fit truth is:
  1. human-confirmed property identity and acquisition-era listing role;
  2. an approved TARGET or NOT_TARGET acquisition-fit label;
  3. immutable evidence bound to the acquisition cutoff;
  4. independent property grouping and existing split membership;
  5. explicit modality availability.
- Physical-condition and modernization labels may remain `UNKNOWN` for this experiment. This does not weaken requirements for those tasks.
- Vision training requires verified acquisition imagery; text requires acquisition remarks; structured requires allowlisted acquisition-time fields. Fusion may use any available trained components and must retain missingness.
- The current `EvidenceHeads` and calibrators can technically omit unsupported axes, but the trainer manifest and release gates still describe all three tasks. A separately approved change is required before training:
  - add immutable `task_scope: [\"acquisition_fit\"]` to the dataset request, run records, release manifest, and report;
  - make acceptance gates scope-aware, evaluating acquisition-fit only for this candidate while marking condition/modernization unavailable—not passed;
  - prohibit an acquisition-fit-only release from serving condition/modernization results;
  - retain the unchanged multi-task gates for every multi-task release.
- Do not freeze or train this cohort until the acquisition-fit scope change and reviewed-label inventory are separately approved.

## Expected Compute and Cost

- Load smoke: CPU only; no GPU.
- Recommended development machine: 2 or more CPU cores, 8 GB RAM, and approximately 4 GB free disk for the isolated environment and frozen encoder snapshots.
- Expected cold duration: about 3–10 minutes after dependencies and snapshots are present; warm load should be under 2 minutes.
- Local incremental compute cost: approximately **$0**. No paid model API is used.
- Network cost is limited to one-time retrieval of the already pinned public encoder snapshots and four private artifacts. If the original SigLIP2 revision cannot be recovered, do not download a replacement.
- A later five-property CPU inference is expected to require minutes, not hours, and no external model API fees; exact time should be measured during the load smoke before approval.

## Required Approval

The next approval is: **approve implementation of Tasks 1–3 and one local, read-only, no-inference load smoke after the original SigLIP2 revision/provenance file has been recovered.**

That approval does not authorize inference, persistent workers, queues, Supabase writes, training, release changes, or deployment.

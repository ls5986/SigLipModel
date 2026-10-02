# SigLipModel — ActVision Training Studio

Image-first review and training tools for property-condition research. This repository
contains the Studio frontend, its local Python API, frozen SigLIP 2 embedding and
classification-head training, historical-listing safeguards, and regression tests.

**Code lives here. Photos, MLS records, human reviews, trained weights, database
backups, and credentials do not.** Existing copies are not deleted by this project.

## ActVision first rebuild (v2, not production-ready)

The primary local/hosted root is now **Label | Review | Train | Releases |
Operations**, with legacy tools under **Advanced**. `/workbench`,
`/property-review`, `/mls-validation`, `/source-rows` and `/status` remain intact.
The local prompt/data surface is `/research`; it is explicitly unavailable on
the lightweight hosted backend. Existing property deep links still open the
detailed reviewer.

The Label workflow reads real property/MLS evidence, retains source/photo-era
warnings and protected-test safeguards, and saves physical condition,
modernization, acquisition fit, confidence and exact text snippets independently.
**Save & Next does not train, call a paid model or promote a release.** Unknown is
not a negative. Typed reviews retain revision history; the cloud backend also
appends linked canonical `review_events` in the same transaction. Removing a
previous text tag appends UNKNOWN, not ABSENT. Changed photos, photo exclusions,
remarks or facts invalidate the labeling evidence fingerprint.
The simplified labeler preserves Advanced reason tags and standout-photo
annotations. Physical evaluation slices use explicit v2 condition (C1-C3
maintained; C5-C6 rough), with the existing legacy-label fallback only for older
reviews without a physical axis. C4 and UNKNOWN are not forced into either slice.

The [shared v2 contract](contracts/README.md) defines request/prediction/feedback/
release schemas, complete evidence identity, explicit missing modality states,
the condition/modernization axes and 17 semantic text tags. Synthetic fixtures
cover unavailable and complete four-component results and release bundles.
`actvision_contract.py` adds cross-field checks beyond JSON Schema. MLS Sourcing
must validate both, bind a response to its request/release, and store predictions
in shadow rather than canonical condition fields.

`text_model.py`, `structured_model.py` and `fusion_model.py` isolate the new
physical-evidence heads. `property_models.VisionEvidenceModel` retains frozen
SigLIP2 bag aggregation; `PhysicalModelAdapter` rejects legacy target models.
The text implementation is an explicitly **TF-IDF supervised baseline**, not a
pretend transformer or keyword-based semantic model. It only learns tags with
explicit positive and negative reviews. Confidence remains null until calibrated.
Structured features preserve JSON numeric types and allowlist categorical fields;
remarks, listing identifiers and future outcomes never enter that matrix.
The existing `v1_models` class paths and feature layout are unchanged for old
joblib artifacts.

### Operator prerequisites and explicit gates

1. Review/apply `supabase/migrations/20261002210000_actvision_v2_contracts.sql`
   **in staging first**, after the existing base/runtime/multimodal migrations.
   This task does not apply migrations or touch live data. Typed cloud saves and
   production feedback fail explicitly until this additive schema is installed.
2. Configure the existing hosted login and a trusted `STUDIO_ROLE`:
   `reviewer` (default), `operator` or `admin`. Only operator/admin may explicitly
   freeze or train, including legacy API routes. This is the existing single-
   principal login with a configured role, not new multi-user identity/RBAC.
3. Preview/freeze use the existing grouped/protected dataset machinery. Verified
   v2 acquisition judgments enter that preview; UNKNOWN and changed/quarantined
   evidence are excluded. Text/physical labels are retained in the manifest.
   Freeze always rebuilds and compares the current label/evidence snapshot
   without saving another preview, including the first v2 review after a legacy
   preview. A new UNKNOWN judgment or changed target invalidates the old preview.
   **Train Candidate still queues the existing legacy target classifier worker.**
   The UI calls it out as legacy; it cannot produce a v2 physical release.
4. Production corrections enter a separate append-only inbox through the
   authenticated, workspace-bound v2 feedback bridge. Same ID/body retries are
   idempotent; conflicting bodies return 409. Review/reject actions append audit
   decisions. Reviewed events still need exact example/era mapping before a
   subsequent training dataset freeze; they are not auto-approved training truth.
5. `release_bundle.py` checks schema, operator approval, protected-slice flag,
   framework/feature compatibility, path containment and artifact SHA256 before
   returning bytes. It does not unpickle arbitrary artifacts or fetch URLs.
   Promotion/rollback remain explicit administrative operations; the new UI
   disables them until evaluation thresholds and the approval workflow are
   provisioned.

**Remaining gates:** reviewed balanced v2 datasets; pretrained semantic encoder
artifacts; grouped OOF training orchestration and calibration for the four physical
components; independently evaluated protected slices and acceptance tolerances;
approved release bundles/runtime adapters; least-privilege service deployment;
production-feedback identity mapping; measured shadow/backfill throughput.
`POST /api/actvision/v2/infer` deliberately returns 503 while those runtime
artifacts/adapters are absent. It never converts a legacy target score into a
physical-condition prediction. No deployment, backfill or expensive training is
started by this rebuild.

Focused checks (no live database or trained weights required):

```powershell
python tools\export_actvision_fixtures.py --check
python -m pytest test_actvision_v2.py test_model_workbench.py test_cloud_runtime.py test_hosted_server.py -q
npm ci
node test_training_studio_browser.cjs
```

The old prompt-lab tests create deeply nested paths relative to the checkout;
on Windows a short-path disposable checkout avoids the OS path-length limit.

## Run the existing Studio from this checkout

Requires Python 3.12. In PowerShell:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

Edit the ignored `.env` file:

```dotenv
ACQ_DATA_ROOT=C:\absolute\path\to\your\existing\ACQVisionPilot
ACQ_EVIDENCE_ROOT=C:\absolute\path\to\your\record-evidence
OPENAI_API_KEY=
```

`ACQ_DATA_ROOT` contains `data`, `artifacts`, `.cache`, and optionally
`historical_samples`. `ACQ_EVIDENCE_ROOT` contains the original
`records.json`, `media.json`, `visual.json`, and `media` directory.
The previous private datasets can be used in place; do not copy them into Git.

```powershell
.\.venv\Scripts\python.exe -B launch.py --open
```

The standalone local reviewer supports optional subjective property ratings.
The hosted Supabase workbook workflow below uses known-target sale/photo verification instead.

The default address is `http://127.0.0.1:8768`. Use `--port 0` for an available
port. Keep the process running. `start_ui.ps1` is an equivalent launcher.
The saved runtime address is under `ACQ_DATA_ROOT`, not in the code checkout.

A code-only clone has **no private dataset or model weights**. Startup reports
missing artifacts explicitly. Either use the existing dataset locations or restore
your private backup first. Some older saved manifests contain absolute paths:
moving those files requires verified path reconciliation, not just copying a pointer.
Absent environment settings, private runtime files default to `~/.siglipmodel`.

## Review workflow

### Acquisition MLS Validation

The Advanced `/mls-validation` page is a focused MLS-validation queue. It includes only
imported examples whose source `match_status` is `candidate` or `unresolved`; base
records already marked `confirmed` are excluded. Each record shows the source
address/APN and transactions, the strongest proposed MLS listing, and at most two
retained interior photos (kitchen and bathroom first). Missing photos remain explicit
placeholders rather than fabricated evidence.

One-click decisions are **Confirm acquisition listing**, **Wrong MLS listing**,
**Right property, wrong era**, and **Unsure**. Every decision uses revisioned
`studio_state` history and advances immediately. Confirmation sets
`certified_for_training` on the validation state; the next frozen training snapshot
includes that selected MLS record without mutating the immutable imported example.
The two photos shown in the validator are a preview only. A confirmed listing whose
MLS metadata reports photos is training-eligible only after the bounded media backfill
has recovered a retained sample (eight photos by default). All retained sample photos enter the frozen
snapshot; non-subject items such as floor plans and shared amenities retain provenance
but are excluded from subject-condition similarity.
The detailed room/condition/opportunity reviewer remains available at
`/property-review`.

- **Review:** property queue, large original photo, room thumbnails, model evidence,
  available-at-review metadata, and an anchored 1–5 target-fit rating. A separate
  Not enough information path prevents missing interiors from becoming negative labels.
- **Photo matching:** review listing/transaction identity separately from target fit.
  Prior-acquisition candidates, ambiguous later sales, and other references are separate.
- **Models & results / Data:** advanced model, prompt-comparison, and import tools.
- **Teach the model about this photo:** approve or correct subject, shared-amenity,
  floor-plan, unrelated and unknown context before optional room/features/preferences.
- **Optional AI second opinion:** previews one explicitly approved paid GPT request.
  Human review does not require it. It is a bounded selected-photo draft, not a
  full photographic inspection.

The hosted queue is organized by the human task, not by historical dataset names:
**Needs review**, **Listing / photos**, **Condition / opportunity**, **Complete**,
and **Needs correction**. Reviewers can also group work by **interior photos**,
**exterior/limited photos**, or **metadata only**. Internal frozen cohorts remain
versioned in the training pipeline but are not exposed as reviewer-facing batches.

### Review UX and model-design principles

- SigLIP/SigLIP2 embeddings and zero-shot prompts are drafts, not human truth; prompt
  wording and calibration matter ([SigLIP](https://arxiv.org/abs/2303.15343),
  [SigLIP2](https://arxiv.org/abs/2502.14786)).
- A property is a bag of photos. One informative image may matter more than the
  average image, so property aggregation should compare max/top-k and eventually
  attention-based multiple-instance learning rather than relying only on mean pooling
  ([Ilse et al., ICML 2018](https://proceedings.mlr.press/v80/ilse18a.html)).
- Missing images are a first-class evidence state. The UI records which modalities
  were used, and models must explicitly support image-only, metadata-only, and combined
  inference rather than inventing missing evidence
  ([Wu et al., 2024](https://arxiv.org/abs/2409.07825)).
- Acquisition-era identity, visible condition, and opportunity are separate constructs.
  Later-sale outcomes and later-renovation photos cannot leak into acquisition labels
  ([Kaufman et al.](https://doi.org/10.1145/2382577.2382579);
  [Jacobs & Wallach](https://arxiv.org/abs/1912.05511)).
- The interface states what the system can and cannot do, shows why a case needs review,
  supports correction, and never silently retrains from a click
  ([Amershi et al., CHI 2019](https://doi.org/10.1145/3290605.3300233)).

An approved property judgment does not approve every photo. Human corrections take
precedence over model drafts. Shared amenities, floor plans, and unrelated images
cannot silently become subject-property condition evidence. A match to a later
resale is not an acquisition-positive label.

## Model and implementation status

### Post-V0 architecture

V0 is preserved as **V0 POSITIVE SIMILARITY BASELINE**. Its immutable identity,
artifact hash, review fingerprint and protected positive-only metrics are stored in
the local baseline registry and revisioned Supabase state. V0 remains a known-target
reference index, not a target/not-target classifier.

The V1 workbench extends, rather than replaces, existing architecture:

- `studio_state` documents retain challenge runs, feedback, error queues, dataset
  previews/freezes, training jobs and candidate summaries with revision history.
- Existing physical-property groups and protected tests remain authoritative.
- The existing local worker executes queued model runs and V1 training; the hosted
  review service never loads Torch or model artifacts.
- Frozen dataset documents are immutable by fingerprint. New feedback requires a new
  preview and dataset version.
- Existing candidate/artifact pointers remain reproducible; V1 artifacts use a
  separate `workbench_candidates` path and never overwrite V0.

### Condition and modernization labels

Physical condition and modernization are independent:

- Physical: `C1_NEW`, `C2_LIKE_NEW`, `C3_WELL_MAINTAINED`,
  `C4_AVERAGE_FUNCTIONAL`, `C5_REHAB_NEEDED`, `C6_SEVERE_DISTRESS`, `UNKNOWN`
- Modernization: `ORIGINAL`, `PARTIALLY_UPDATED`, `UPDATED`,
  `FULLY_REMODELED`, `UNKNOWN`

These are acquisition-oriented internal training labels informed by UAD concepts,
not licensed appraisal determinations. UNKNOWN stays masked and is never converted
to a negative.

### Model Workbench

The hosted development workbench at `/workbench` supports:

1. Search the current training cohort or browse the approved MLS sourcing project
   by active listing, address/city/ZIP/listing ID, deterministic random five, or
   current opportunity five. Training and protected-test overlaps are excluded.
2. Freeze a named MLS challenge batch. Its sanitized metadata and up to eight
   representative selected photos are hashed by content; later runs fail if the
   source evidence changes.
3. Queue V0 metadata/image/combined scoring on the local worker.
4. Optionally decide TARGET/NOT_TARGET/UNKNOWN before revealing predictions.
5. Inspect component scores, evidence mode, nearest known targets and influential
   photos.
6. Save explicit target feedback, hard negatives, condition/modernization corrections
   and photo exclusions without retraining. Fixed-challenge feedback remains isolated
   unless **Promote to Dataset Vnext** is explicitly selected; UNKNOWN cannot promote.
7. Review false-positive, false-negative, high-confidence-wrong, candidate-regression,
   disagreement, metadata-only, limited-visual and hard-negative queues.
8. Preview Dataset Vnext with exact added/removed/changed examples, split counts and
   before/after protected-test hashes.
9. Explicitly freeze the next dataset version.
10. Queue one local metadata/vision/fusion training job with persisted stages.
11. Compare any two immutable saved candidates. V0 is clearly marked as a
    positive-similarity baseline, not a classifier accuracy result.

V1 metadata uses a versioned pre-decision feature policy plus deterministic TF-IDF
remarks. Structured evidence includes DOM, MLS photo count, list/original-price
reduction, property facts, coarse geography, and transaction-history count/recency/
price only for sales strictly before the frozen listing snapshot. Current acquisition
closes and later resales are excluded as outcome leakage. V1 vision compares mean,
max and mean+max frozen SigLIP property aggregation
using validation data only. Fusion is fit from deterministic grouped out-of-fold
metadata and vision predictions from training groups; validation selects the visual
aggregation, and the protected test is read only after selection. Fusion records
metadata/vision availability and image
coverage, falls back to metadata when images are absent, and never penalizes a
property merely because it has only one or two usable photos.

The MLS challenge adapter reads only `mls_properties`,
`mls_property_intelligence`, `mls_opportunities` and `mls_run_properties` from
Supabase project `uxlfqsgynbbgspaakzey`. It validates both the Supabase URL and
database identity, starts every transaction with `SET TRANSACTION READ ONLY`, and
never treats an existing opportunity score as ground truth. Configure
`MLS_SOURCE_DATABASE_URL`, `MLS_SOURCE_SUPABASE_URL`, `TRESTLE_CLIENT_ID` and
`TRESTLE_CLIENT_SECRET` on the development service. Production remains untouched.

The initial metadata classifier is regularized logistic regression over sparse
structured/TF-IDF features. This intentionally reuses the pinned scikit-learn
environment, remains inspectable with the current small labeled-negative set, and
avoids adding a large CatBoost runtime before class coverage exists. Compare CatBoost
as a later candidate after enough explicit negatives/hard negatives are collected;
do not replace this baseline without protected and challenge-set evidence.

#### Workbench clicks

1. Open **Model Workbench → Test a property**.
2. Choose **Confirmed dataset** for an existing record, or choose **Active MLS**,
   **Random 5 unseen listings**, or **Top 5 current opportunities**.
3. For unseen MLS results, name and freeze the visible set. Choose the resulting
   **Fixed batch** source so every rerun uses the same metadata/photo hashes.
4. Optionally enable **Blind review**, and choose **RUN CURRENT MODEL**. Run and
   train buttons remain disabled while the local worker heartbeat is stale/offline.
5. Review Metadata, Vision and Combined scores, mode used, condition/modernization,
   nearest known targets and top visual evidence.
6. Save **YES / NO / UNSURE**, optional **Hard negative**, condition/modernization,
   explanation and excluded photos. Challenge feedback is challenge-only by default;
   explicitly check **Promote to Dataset Vnext** only after review. For
   room/context/wrong-era corrections choose **Correct rooms, photos or era**.
7. Open **Review errors** to filter false positives, false negatives, disagreements,
   metadata-only, limited-visual and hard-negative cases.
8. Open **Dataset versions → Preview Dataset Vnext**. Review counts and added,
   removed and changed examples. Freeze explicitly.
9. Open **Train → TRAIN NEW CANDIDATE**. The button remains disabled until train,
   validation and protected-test class coverage is sufficient.
10. Open **Compare** to select any two immutable saved versions. Return to
    **Test a property** to rerun the same fixed challenge batch.

The image backbone is `google/siglip2-base-patch16-224`; it stays frozen while
lightweight classifiers learn photo context, room, visible-feature and
photo-preference labels. Reviewed property targets can additionally fit:

- an image-only property head over pooled usable-photo embeddings;
- a metadata-only head over an allowlisted pre-decision MLS feature schema; and
- a fusion head over the two component scores and evidence coverage.

Each component is optional. A candidate records which components were actually
fit; unavailable requested modes return insufficient evidence instead of silently
using another model.
The exact downloaded revision and model hashes belong in private artifact metadata.
Unknown labels are not negatives. Training uses grouped splits and preserves
protected test groups; user-provided cohort labels are not independent gold labels.

The Studio includes a GPT-generated **draft** whole-property assessment with context
and coverage guards. Property heads remain review-priority experiments, not proven
investment or comp/economics recommendations. Raw photo and property scores are not
profit probabilities, and every property result remains `NEEDS_REVIEW`.

This is currently a **loopback-only local application**, not a hosted service.
Supabase record/storage migration is separate from deployment. Uploading backups
does not automatically switch the Studio to remote data. Hosted authentication,
restricted runtime credentials, remote workers, and verified cutover remain work
to complete before deleting local datasets.

The draft training schema supports immutable review events, frozen datasets,
protected evaluation slices, component model runs, explicit release records and
prediction modes. Applying that schema still requires a reviewed delta migration;
it does not update an already-deployed Supabase project by itself.

## Source layout

| Files | Responsibility |
|---|---|
| `launch.py`, `review_ui.html`, `review_queue.py` | Focused image-first UI, thumbnails and queue |
| `studio_api.py`, `review_server.py` | Local API, same-origin token checks, saved reviews |
| `assessments.py`, `prompt_lab.py` | Explicit GPT drafts and frozen prompt comparisons |
| `historical_store.py`, `acquisition_policy.py`, `photo_view.py` | Listing provenance and unified photo labels |
| `studio_data.py` | Durable SQLite catalog and bounded import |
| `pilot.py`, `studio_worker.py`, `train_reviewed.py` | Embeddings and trained heads |
| `property_models.py` | Allowlisted metadata features, pooled vision features and modality routing |
| `studio_jobs.py`, `guarded_jobs.py`, `training_jobs.py` | Versioned training previews, jobs and eligibility |
| `tools/migration` | Administrative database/storage backup and restore tooling |

## Tests

```powershell
.\.venv\Scripts\python.exe -B -m pytest -q
```

Default tests use synthetic/temporary data and mocked paid providers; they do not
train a real model or need your private dataset. Browser regression testing:

```powershell
npm ci
npx playwright install chromium
.\.venv\Scripts\python.exe -B verify_browser.py
```

`requirements.lock.txt` records the original pilot environment; `requirements.txt`
is the supported installation entry point. Browser dependencies have their own
`package-lock.json`.

## Safety

- Never commit `.env`, API keys, MLS data, photos, review exports, or model binaries.
- OpenAI credentials come from this checkout's ignored `.env` or environment only;
  the Studio no longer reads credentials from the MLS application repository.
- Do not expose the loopback server publicly. Its local token is not hosted auth.
- Only load trusted model artifacts: `joblib` files can execute Python code.
- Follow source-image licensing, retention, and training rights.
- Migration is administrative and explicit. Database rows and storage objects must
  be verified independently. No script here authorizes deletion after upload alone.


## Connect the review → training → ACQ BOT comparison loop

1. In **Photo matching**, verify acquisition-stage evidence. Quarantine renovated
   resale, wrong-listing and uncertain-era photos. A successful investment is not
   an approved label for every photograph.
2. In **Review**, approve/correct individual rooms, visible features and target
   photo preferences. Rate the property independently on the anchored 1–5 scale,
   record evidence/confidence, and select only standout photos. Drafts and
   whole-property judgments do not supervise photo preference classifiers.
3. Open **Models & results → Check whether my reviews are trained**. Review saves
   labels, not weights. Use **Preview local training → Train local candidate**.
   Older candidates require one new training run to establish the review fingerprint.
   Training rebuilds reviewed heads from the baseline plus currently eligible labels;
   an obsolete reviewed preference head is never inherited when its labels disappear.
   Features not refitted remain draft baseline features, not newly human-trained facts.
4. In ACQ BOT dev, open a property → **Evidence → Compare your SigLIP model** and
   download its test request. Administrator access and a saved photo assessment are required.
5. Select that JSON in Studio's **ACQ BOT comparison** section. Preview the exact
   candidate and image hosts; confirm the download and local run. Up to 12 public
   HTTPS photos, 4 MB each, are processed one at a time in a separate subprocess.
   No OpenAI calls, production writes or new hosted service. Temporary photos are
   removed on success/failure. A timed-out worker may leave bounded temporary files
   in that comparison folder; these are not training data.
6. Download the result and upload it to the same ACQ BOT property. The app records
   the weights checksum, backbone revision, review fingerprint and photo byte hashes.
   The live condition, comps, valuation and memberships do not change.

Comparisons are manually imported local experiments, not signed attestations. They
show uncalibrated **photo preference**, not whole-property renovation severity.
Saved baseline URLs can change contents; byte equality to the historical assessment
is not proven. Duplicate-image detection is partial, and unseen hashes do not prove
unseen physical properties. Independent validation, property-level aggregation,
performance benchmarking and any production promotion remain separate gates.

## Property inference modes

`acq-property-request-v2` accepts an allowlisted metadata snapshot, zero to twelve
photos, and one requested mode:

- `automatic`
- `images_only`
- `metadata_only`
- `images_and_metadata`

Automatic mode prefers fusion, then images, then metadata. Explicit modes never
fall back silently. The result includes `mode_used`, all available component
scores, evidence confidence, usable-photo count, metadata completeness, warnings,
component versions and the immutable request/model identities. The original
photo-only `acq-siglip-request-v1` remains accepted for compatibility.

Only pre-decision metadata fields are accepted. Close price/date, later resale
outcomes and arbitrary MLS fields are rejected from the v2 comparison request.

## Evaluation and rollout

Split and evaluate by physical property group, never by individual photo. Duplicate
image hashes and linked listing identities remain in one group. Keep protected
sets for:

1. photo context, including hard floor-plan and shared-amenity negatives;
2. properties with both images and metadata;
3. metadata-only or missing-interior listings;
4. image-dominant listings with sparse metadata; and
5. a temporal shadow cohort newer than the training cutoff.

Training reports grouped internal validation separately from protected slice
metrics. Reviewers may inspect protected examples, but those labels do not train
the current candidate. Create a new versioned dataset before deliberately promoting
previous holdout feedback.

Roll out a candidate in shadow mode first: score real incoming MLS snapshots, save
the result and model identity, but do not change sourcing decisions. Promotion to
an approved model release remains an explicit administrative action after slice
metrics and failure cases are reviewed.

Update this code checkout and restart `launch.py` while keeping `ACQ_DATA_ROOT` and
`ACQ_EVIDENCE_ROOT` pointed at existing private folders. Updating GitHub does not
update a running Windows process. Supabase backup data is not a live Studio connection.

## Automatic dev connection (recommended)

After updating and restarting Studio, open **ACQ BOT dev on the same computer**.
On a property's Evidence tab, choose **Connect Vision Studio**. Approve the local
connection page once. If Studio uses a non-default port, enter it in Connection
settings first. The one-time code expires after 10 minutes and is removed from
the local browser address bar. No Supabase keys or ACQ BOT account tokens are copied.

Then **Run my SigLIP model** in ACQ BOT queues that property, runs it locally and
returns the versioned comparison automatically. File download/upload is only a
fallback. You can initiate tests from your phone after connecting the computer,
but the computer and Studio server must remain running. This is outbound HTTPS;
no tunnel, public local server or hosted model service is required.

Studio checks for work every 30 seconds when idle (10 seconds during a test).
Model review status is cached for at most 60 seconds, then checked again before
inference. One test runs at a time; ACQ BOT permits five queued/running tests per
workspace. The model identity is pinned when queued, so a changed candidate causes
an explicit failure instead of silently using different weights. Training and
live property updates are never automatic. A stopped/restarted worker reports
interrupted work rather than silently repeating inference. A failed result upload
retains the completed local result and retries delivery idempotently.

Cancel a test in ACQ BOT; cancellation reaches Studio at the next heartbeat and
terminates its comparison subprocess. Revoke the connection in ACQ BOT or use
**Disconnect this computer** in Studio. The scoped comparison credential expires
in 30 days; ACQ BOT stores only its hash. Studio keeps the credential in the private
data root (`data/acq_connection.json`), never in Git or the browser. Do not share
that file. If a local disconnect cannot reach the server, revoke it in ACQ BOT too.

The connected worker is restricted to the ACQ BOT dev origin. ACQ BOT rejects
these endpoints outside staging. Supabase remains storage; local Studio still
uses the configured local runtime folders. Real-model/Windows acceptance and any
production promotion remain separate steps.

## Supabase-backed review data (development)

`STUDIO_DATA_BACKEND=supabase` runs the image review UI against the private
`acq_training` schema. It does **not** require the original Windows dataset,
`studio.sqlite3`, local review JSON, embeddings, or model weights to review photos.
The local backend remains the default. Switching modes is explicit; cloud failures
never fall back to an older local copy.

Install the normal runtime dependencies plus `requirements-cloud.txt`. Apply the
versioned Studio state migration to the **training project only**. It expects the
existing migrated `acq_training` schema and roles. This is not an ACQ BOT production
migration or an empty-project bootstrap.

Set server-side environment variables (never commit credentials):

```dotenv
STUDIO_DATA_BACKEND=supabase
SUPABASE_PROJECT_REF=<training-project-reference>
STUDIO_DATABASE_URL=<TLS-Postgres-connection-for-a-restricted-training-login>
STUDIO_WORKSPACE_ID=<existing-training-workspace-uuid>
STUDIO_STORAGE_SECRET=<training-project-server-storage-credential>
STUDIO_CACHE_DIR=<absolute-path-to-disposable-photo-cache>
```

The database login must inherit `acq_training_reviewer`, have a matching row in
`acq_training.principals`, and have neither superuser nor BYPASSRLS privileges.
Use the training project's own connection information; never reuse the acquisition
production database credentials. Startup validates workspace access and schema.
A database administrator provisions this login and supplies its password through
server environment configuration. The Storage credential stays server-side.

Start `launch.py` normally. The server **still binds only to loopback**. This change
is the data adapter, not public hosting or authentication. Do not expose this
server publicly. Hosted login and background model workers are separate work.

Supported now:

- Deduplicated acquisition review queue and listing/photo detail from Supabase.
- Private photos fetched on demand, SHA-256 verified before display, with a bounded
  256 MiB disposable cache. Revocation/retention is checked before serving even
  cached thumbnails. Missing/corrupt photos fail explicitly.
- Original backed-up human labels remain available as the baseline. New image,
  property, and photo-era reviews are committed to `studio_state` and append-only
  revision history. SQL compare-and-swap rejects stale saves across processes.
- Existing prior-acquisition matching and wrong-era quarantine rules remain in force.
  Cohort membership does not approve photo labels. Protected test photos stay marked.
- A versioned document API is available to the future remote job runner; model jobs
  are not automatically started by reviewing or saving a label.

Cloud review mode currently disables model training, GPT scoring, local pairing,
legacy tools and imports. Those still work in the original local mode. This avoids
mixing cloud corrections with stale local training snapshots. Cloud worker/artifact
integration is the next cutover, followed by hosted authentication and Render setup.
Existing local files are preserved; do not delete them until model artifact recovery
and the complete hosted workflow have been accepted.

Validation: run `pytest -q`. Live verification should commit one synthetic document,
read it using a fresh connection, reject an outdated revision, and reject a different
workspace under the restricted role. No real label needs to be changed for this test.
A fresh cache should recover a known private photo with the saved hash; that final
Storage credential check must run in the configured deployment environment.

## Supabase known-target learning and MLS dev comparison

The imported workbook is the known-target source. All 618 source rows remain
in Supabase, including unresolved records and repeated parcels. `/source-rows`
shows every row, its stored candidate listings, acquisition dates, retained
photo coverage, verification status and durable correction notes. Notes do not
replace listing/photo evidence or silently approve a row. Unresolved/wrong-sale
matches need rematching and missing acquisition photos need recovery before use.

The initial 250 listing keys remain a convenient photo-review batch at
`/?cohort=training`; they are no longer the training cap. The hosted reviewer
opens in sale/photo verification mode. Overall target/pass ratings and negative
examples are not required. Existing subjective ratings remain stored separately
and do not override the workbook's known-target provenance.

Confirm the selected MLS listing is the right acquisition sale and the images
show the property in that era. Verification is bound to the photo-byte hashes:
changing the photo set invalidates it. Quarantined, unverified, withdrawn and
expired evidence stays out. Rooms/features are optional independent annotations;
a positive property never automatically becomes a rough room or a preferred photo.

The first candidate is a **positive-only reference similarity model**, not a
binary classifier or a fine-tuned SigLIP encoder. It fits a versioned reference
index from frozen SigLIP image embeddings and descriptive metadata, including
numeric scales from training references only. It returns image similarity,
metadata similarity and their equal-weight combination against the same
reference, with up to five independent nearest examples and workbook row IDs.
The score is not target probability, condition severity or profitability.
Price, days on market, sale outcomes and reviewer annotations are not features.
The original sanitized listing snapshots remain retained for provenance.

The nine initial metadata groups are year built, bedrooms, bathrooms, living
area, lot size, property type, city, state and postal code. Missing data remains
missing; metadata similarity requires at least three shared fields. Full listing
remarks are retained but this initial model does not embed their text.

Photo coverage is saved independently on the versioned Supabase era review as
`unknown`, `interior_available`, or `no_interior`, and is bound to the photo hash.
A verified target with no interior photos remains eligible for metadata and visual
learning from relevant exterior/outdoor photos. Irrelevant images are excluded separately.
Missing interiors never become a negative condition/target label. Older reviews
remain coverage-unknown. Changing coverage invalidates the dataset fingerprint.

Physical-property groups and exact image aliases cannot cross train/evaluation
boundaries. Original protected test groups and the initial batch holdout are
preserved. Stable hash membership protects additional groups as rows are recovered.
Evaluation reports heldout coverage and similarity, not accuracy, AUC or false
positive rates. Validate actual ranking usefulness on fresh MLS dev properties.

The hosted Render app saves reviews and remains lightweight. On the trusted
computer containing the restored backbone, baseline room heads, embeddings and
manifest, use the existing server-only Supabase configuration plus:

```powershell
$env:STUDIO_DATA_BACKEND = "supabase"
$env:STUDIO_MODEL_WORKER = "1"
# ACQ_DATA_ROOT points to the existing restored model/data folder.
python review_server.py --open
```

`/status` allows a candidate preview when at least five independent verified
training groups and two verified protected evaluation groups exist. This is an
exploratory minimum, not evidence of model quality. Pending records remain
excluded, so you can verify batches without finishing every source row first.
The preview freezes source lineage, metadata, hashes, annotations and exclusions;
changed verification or review state requires a new preview.

Connect the local worker from MLS dev using **Connect Vision Studio**. The
existing property request protocol returns separate image/metadata/combined
similarity and nearest source examples. Unavailable explicit modes report
insufficient evidence. The experiment never overwrites the live MLS assessment,
automatically retrains or promotes a production model.

### Automatic room and photo-type suggestions

The cloud reviewer queues an automatic labeling request when you open a photo
set. This runs separately from training and sale verification: no overall ratings,
room labels, silver heads or embedding manifest are needed to start. No OpenAI
key, paid API call or image upload to Hugging Face is used. The frozen public
SigLIP image/text model runs on your computer; the first run downloads its model
and tokenizer if they are not already cached. Give this worker enough memory for
the full image/text checkpoint; the lightweight hosted reviewer does not load it.

With the existing restricted worker Supabase/storage configuration and Python
environment, run one worker per workspace:

```powershell
python -m pip install -r requirements.txt -r requirements-hosted.txt
python cloud_autolabel.py
```

Leave it running while reviewing in the hosted app. It reads queued properties,
classifies retained photos in batches of four, and saves editable suggestions in
Supabase. `--once` processes the queued requests and exits. The UI reports queued,
running, failed or completed work and whether the worker is connected. It refreshes
room suggestions without resetting an unsaved interior-coverage selection.

Room and photo-type prompts are versioned as `siglip-room-context-v1`. Suggestions
are bound to the exact photo hashes, checkpoint revision and prompt hash. The
heuristic score/margin gate abstains on ambiguous images; these scores are not
calibrated confidence. Pools and recreation areas do not establish private versus
HOA ownership. Context and room suggestions never verify the sale, approve a
property, write reviewed labels, fit weights or promote a candidate. Human
corrections take priority. Condition/features remain unknown unless reviewed.

Worker requests/results use the existing workspace-restricted, revisioned
`acq_training.studio_state` JSONB records and history. No new public table or
permissions are needed. Changed/expired photo evidence is checked again before
results are published. Failed requests can be retried from the review page.


### SigLIP rooms and a small ChatGPT labeling test

The development Blueprint uses `STUDIO_AUTOLABEL_PROVIDER=hybrid`.
SigLIP supplies rooms and photo-type drafts. ChatGPT supplies visible condition and feature drafts;
the ChatGPT response schema does not include a room field. Human room corrections take priority.

Set `OPENAI_API_KEY` as a secret on `acq-vision-studio-dev` and save/redeploy. The key stays server-side.
The hosted ChatGPT worker uses `gpt-4.1-mini` and a default limit of 100 calls per UTC day.
Opening a property only queues SigLIP rooms. It does not start paid calls. After rooms are ready,
choose **Test ChatGPT tags on up to 8 photos** to label at most eight selected photos, in batches of four.
No bulk labeling is enabled. Review the resulting tags and actual token usage before expanding.
Drafts and per-photo caches stay separate from human approvals; cached tags are reused.
Failures and interrupted paid requests require an explicit retry.

SigLIP still needs the existing model-capable worker with the private Supabase environment:

```bash
python -B cloud_autolabel.py
```

Its default provider is hybrid. Install `requirements.txt` for that worker. The 512 MB Render review
service deliberately installs `requirements-hosted.txt` and does not load Torch or a SigLIP checkpoint.
Setting an OpenAI key alone does not start the SigLIP worker. The UI reports a missing room worker.
To keep a legacy room-only setup, set `STUDIO_AUTOLABEL_PROVIDER=siglip` on both runtimes.

Photo thumbnails now have **Use photo** checkboxes. Confident floor plans, documents/maps, unrelated
images and shared amenities are automatically unchecked; uncertain photos stay selected for review.
The checkbox is a human override and does not approve any room or condition label.

All workbook properties remain known targets once their acquisition sale/photo association is verified.
**No interior photos** records coverage only. Relevant exterior/outdoor images still teach visual similarity,
and metadata still contributes. Interior condition is unknown without visible interior evidence.
Actual absence of usable images falls back to metadata. Price never enters target similarity inputs.
Photo condition labels are stored for review; the current trainer does not train a separate overall-condition head.


### First 2026 sale correction

Use the earliest actual 2026 sale for each physical house, including repeated workbook rows.
Recording dates never substitute for actual sale or MLS close dates. Later-sale images are hidden
from acquisition review and cannot be approved or queued for labeling. Original records remain retained.
For 17633 Corte Potosi, the target sale is January 13, 2026; the saved April 25 listing is not that sale.
The January listing/photos need recovery; missing first-sale evidence does not change the house's target label.

For a house with multiple actual sales in 2026, use its first 2026 sale. A single
2026 resale does not replace a valid 2025 acquisition example. Candidate close
dates cannot compete with themselves as independent workbook sale dates.

### One local Copilot + SigLIP labeling worker

Use Python 3.11+ in your existing working SigLIP virtual environment. Do not reinstall Torch.

```powershell
python -m pip install -r requirements-copilot.txt
python -m copilot download-runtime
python local_label_worker.py --check
python local_label_worker.py
```

Before `--check`, fill the local `.env` entries `STUDIO_DATABASE_URL` and
`STUDIO_STORAGE_SECRET` using your development database connection and existing
private storage secret. Keep the existing `SUPABASE_PROJECT_REF` and
`STUDIO_WORKSPACE_ID`. Never commit secrets. Install the interactive CLI with `winget install GitHub.Copilot` on Windows
(or `npm install -g @github/copilot` with Node.js 22+). Copilot uses your own
GitHub Copilot CLI login: open `copilot`, enter `/login`, and complete account sign-in locally.
If you use a separately installed CLI, `COPILOT_CLI_PATH` can point at it.
VS Code Copilot sign-in alone is not a guarantee the CLI is signed in.
`STUDIO_COPILOT_MODEL` defaults to `gpt-4.1`; preflight lists alternatives if that
vision model is unavailable. Copilot requires plan/model access and can consume
Copilot credits; it does not need `OPENAI_API_KEY`.

The single worker first classifies rooms with local SigLIP. In the review app,
choose **Local Copilot**, then **Test draft tags on up to 8 photos**. Only explicit
test requests trigger Copilot inference. Hosted OpenAI workers cannot claim
Copilot tests. Sessions are fresh per batch, tools are disabled, photo labels are
cached by provider/model/image/room, and human review is still required.
`--once` processes currently queued requests and exits. `--check` performs no
labeling inference. It checks database access, CLI authentication and vision
model availability before loading SigLIP.

### MLS remarks and synthetic photo exclusion

Sale-specific MLS remarks are preserved in property training snapshots and shown
in the review app. `python listing_text_report.py` computes recurring words and
phrases among supported sale matches, deduplicates physical groups, strips
price/financing clauses, and saves the result as `mls-phrase-report` in Supabase.
These are descriptive cohort patterns, not a validated text prediction model.

Explicit AI imagery, virtual staging or digital rendering disclosures in MLS
remarks exclude the entire attached photo set until original photos are
identified. Photo-caption disclosures exclude that photo. Exclusion applies to
both automatic labeling and visual training, including previously approved
condition labels. A manual similarity checkbox cannot bypass it. The target
property and metadata remain eligible once its sale is verified; excluded images
remain available for inspection. Original MLS text and photos are never deleted.

### Run all applicable photo sets overnight with local Copilot

After connection preflight succeeds, this explicit command queues all retained,
supported acquisition listings and labels **all selected original photos**, not
just the eight-photo tests:

```powershell
.\.venv\Scripts\python.exe local_label_worker.py --check
.\.venv\Scripts\python.exe -u local_label_worker.py --enqueue-all
```

Keep the attached terminal open and your laptop awake and plugged in. Room-only
queued requests are upgraded to full Copilot requests. Completed full requests
are skipped on another invocation, cached image drafts are reused, and existing
in-flight requests are not overwritten. Wrong-sale/era records, unavailable
photos, and MLS AI/virtual-staging disclosures are excluded. Human verification
and training approval are not performed by this command. The worker prints batch
progress and continues waiting for further queued work; Ctrl+C stops it.

`STUDIO_COPILOT_MAX_CALLS_PER_DAY` is separate from the hosted OpenAI budget and
defaults to 10,000 four-photo calls, a finite limit for a full overnight batch.
Copilot plan credits/model availability still apply. Paid failures are saved and
are not automatically retried in the worker loop; another explicit enqueue can
resume failed records using their cache. The full command enqueues supported
candidate photo sets across the imported workbook, not arbitrary live MLS listings.

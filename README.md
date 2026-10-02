# SigLipModel — ACQ Vision Studio

Image-first review and training tools for property-condition research. This repository
contains the Studio frontend, its local Python API, frozen SigLIP 2 embedding and
classification-head training, historical-listing safeguards, and regression tests.

**Code lives here. Photos, MLS records, human reviews, trained weights, database
backups, and credentials do not.** Existing copies are not deleted by this project.

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

An approved property judgment does not approve every photo. Human corrections take
precedence over model drafts. Shared amenities, floor plans, and unrelated images
cannot silently become subject-property condition evidence. A match to a later
resale is not an acquisition-positive label.

## Model and implementation status

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
A verified target with no interior photos remains eligible for metadata learning;
its photos are retained for review but excluded from this training candidate.
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


### Hosted automatic draft tags

The development Render web service now runs an OpenAI draft-label worker alongside the review app.
Set `OPENAI_API_KEY` as a secret environment variable on `acq-vision-studio-dev` and save/redeploy.
The key stays server-side; the Blueprint declares it with `sync: false`. No laptop worker is needed.
`STUDIO_AUTOLABEL_PROVIDER=openai` selects hosted drafts; `STUDIO_OPENAI_MODEL` defaults to
`gpt-4.1-mini`, and `STUDIO_OPENAI_MAX_CALLS_PER_DAY` defaults to 100 API calls (up to four photos
per call). This is a call limit, not a dollar budget. Set a project budget separately in OpenAI.

Opening a property queues its retained photos, with no bulk labeling at startup. The worker sends
resized photos only: no price, listing metadata, target judgment, or sale verification is sent.
Room, photo context, condition, and visible feature drafts are cached by photo content and model/policy.
Drafts never approve a sale or train a head. Review and use **Approve photo tags** to save explicit
human labels; human corrections take priority. Photo condition labels are recorded for review;
the current trainer does not yet train a separate overall-condition head.
Failures do not automatically retry paid calls; an interrupted request requires an explicit retry.
Changing model/policy regenerates drafts. Missing keys leave property verification available.
The existing local SigLIP worker remains available with `STUDIO_AUTOLABEL_PROVIDER=siglip`.

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

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
  and Agree / Correct / Can't tell actions.
- **Photo matching:** review listing/transaction identity separately from target fit.
  Prior-acquisition candidates, ambiguous later sales, and other references are separate.
- **Models & results / Data:** advanced model, prompt-comparison, and import tools.
- **Score this property:** previews one explicitly approved paid GPT request. It is
  a bounded selected-photo draft, not a full photographic inspection.

An approved property judgment does not approve every photo. Human corrections take
precedence over model drafts. Shared amenities, floor plans, and unrelated images
cannot silently become subject-property condition evidence. A match to a later
resale is not an acquisition-positive label.

## Model and implementation status

The image backbone is `google/siglip2-base-patch16-224`; it stays frozen while
lightweight classifiers learn room, visible-feature and photo-preference labels.
The exact downloaded revision and model hashes belong in private artifact metadata.
Unknown labels are not negatives. Training uses grouped splits and preserves
protected test groups; user-provided cohort labels are not independent gold labels.

The Studio includes a GPT-generated **draft** whole-property assessment with context
and coverage guards. It does **not** yet provide a proven whole-property investment
classifier or an integrated comp/economics recommendation. Raw photo scores are not
profit probabilities.

This is currently a **loopback-only local application**, not a hosted service.
Supabase record/storage migration is separate from deployment. Uploading backups
does not automatically switch the Studio to remote data. Hosted authentication,
restricted runtime credentials, remote workers, and verified cutover remain work
to complete before deleting local datasets.

## Source layout

| Files | Responsibility |
|---|---|
| `launch.py`, `review_ui.html`, `review_queue.py` | Focused image-first UI, thumbnails and queue |
| `studio_api.py`, `review_server.py` | Local API, same-origin token checks, saved reviews |
| `assessments.py`, `prompt_lab.py` | Explicit GPT drafts and frozen prompt comparisons |
| `historical_store.py`, `acquisition_policy.py`, `photo_view.py` | Listing provenance and unified photo labels |
| `studio_data.py` | Durable SQLite catalog and bounded import |
| `pilot.py`, `studio_worker.py`, `train_reviewed.py` | Embeddings and trained heads |
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
   photo preferences. Drafts and whole-property judgments do not supervise photo
   preference classifiers.
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

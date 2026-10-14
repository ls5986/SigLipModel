# ActVision v2 development load smoke

This command verifies and loads the existing ActVision v2 candidate without
running inference. It does not load application environment files, connect to
Supabase, start a worker, enqueue work, write remote state, train, promote a
release, or deploy anything.

## Required original assets

Use release `f134590b-122b-42dc-9a27-209523e2a33c` only.

Prepare a read-only local bundle:

```text
bundle/
  release.json
  models/actvision-v2/f134590b-122b-42dc-9a27-209523e2a33c/
    vision.joblib
    text.joblib
    structured.joblib
    fusion.joblib
```

`release.json` must be the release registry's canonical `bundle_manifest`.
Retrieve the four artifacts through an authorized read-only storage operation.
The smoke verifies the canonical bundle SHA and the four recorded artifact
hashes before deserializing anything.

The pinned text checkpoint is:

- model: `sentence-transformers/all-MiniLM-L6-v2`
- revision: `1110a243fdf4706b3f48f1d95db1a4f5529b4d41`
- checkpoint SHA-256:
  `dfdac870e42d2700ab4813bb3ad1dab8c9f992d4a99d5f50e4f5732610d30d92`

The vision input must be the original worker's `artifacts/backbone.json` and
the exact local snapshot referenced by that file. The snapshot directory name,
model identity, full revision, weight sizes, and weight hashes are verified.

## Current vision-provenance blocker

The v2 release manifest and its vision model-run record contain only
`google/siglip2-base-patch16-224`; they do not contain the Hugging Face commit
revision. The training request and worker heartbeat also omit it. No original
`backbone.json` or matching SigLIP2 snapshot was found in the repository,
available local caches, development directories, or retained Copilot session
files.

Do not use the current Hugging Face HEAD or the later paired-runtime revision
as a substitute. Recover `artifacts/backbone.json` and the referenced snapshot
from the original model worker's persistent disk/cache or a verified backup.
If that cannot be recovered, the exact v2 vision pipeline is not reproducible
and the load smoke must remain blocked.

## Runtime

Create an isolated Python 3.12.14 environment with the exact versions in
`requirements-label-worker.txt`, including CPU Torch 2.6.0. The loader also
checks:

- scikit-learn 1.6.1
- joblib 1.4.2
- Transformers 4.57.6
- sentence-transformers 5.1.2

Run from the repository root without sourcing an application `.env`:

```bash
python tools/smoke_actvision_v2_load.py \
  --bundle-root /absolute/path/to/read-only-v2-bundle \
  --vision-provenance /absolute/path/to/original/artifacts/backbone.json \
  --text-checkpoint /absolute/path/to/pinned/text/checkpoint
```

Success prints JSON containing all four loaded component classes, artifact
hashes, feature schemas, calibration versions, both encoder identities,
runtime versions, elapsed time, peak memory, and
`"inference_executed": false`.

## Five-property follow-up

After this load smoke succeeds, request separate approval for local inference
on acquisition listings `1120072525`, `1151195349`, `1147462608`,
`1150789139`, and `1156423432`.

Before scoring, create a read-only evidence manifest for each listing that
binds its acquisition listing key, acquisition cutoff, snapshot hash, selected
acquisition-photo hashes, exact remarks, structured fields, and missing
modalities. Reject media or text outside that binding. Never substitute resale
photos, resale remarks, resale prices, or future outcomes.

The first scoring run should be a non-persistent local process whose output is
a local session report showing Vision, Text, Structured, and Fusion results.
It must not enqueue work or write predictions to Supabase. Persistence and UI
review integration require separate approval.

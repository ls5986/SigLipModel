# PI Model Lab Studio preview

An isolated **read-only** parallel entry preserving the existing Studio frontend and hosted Python service. It does not use old Model Training Supabase, old Studio cookie APIs, or old label worker.

## Build

`npm ci && npm run build:pi-studio` at repository root. The entry is `pi-studio-dist/pi-studio-preview.html`. On a static host, copy that file to `index.html`.

Requires build-time `VITE_PI_API_ORIGIN=https://property-intelligence-dossier-dev.onrender.com`, `VITE_PI_SUPABASE_URL=https://bveulyklycgxbnepehis.supabase.co`, and the **public** PI DEV publishable key in `VITE_PI_SUPABASE_PUBLISHABLE_KEY`. Do not provide database URLs, access tokens, or service keys. Login uses the PI DEV Supabase Auth REST endpoint with memory-only access tokens (no token persisted).

## What is enabled

- Authenticated Model Lab catalog v2 bounded pages for datasets, model runs, artifacts, evaluations and releases.
- Current property triage, recorded evaluations, and disagreement queue metadata. These are *scoped*, partial views, not global counts.
- Original Studio's conceptual sections: Overview, Model Review, Training, Runs & Releases.
- No POST, model execution, human review write, freeze, training launch, artifact fetch or promotion.
- Backend response errors, missing auth / 403 or missing v2 catalog read are visible, not replaced with fixtures.

## Not a complete migration

This first preview is not yet the old paired-image annotator with PI-backed Save & Next. Preserve original SigLip main and current Render studio and worker. A separate next integration should bind exact PI property/listing/snapshot/photo evidence and correctly map immutable human labels using PI individual reviewer APIs, then implement guarded admin training actions after tests. Do not call the legacy hosted training endpoints from this preview.

Hosted signed-in browser acceptance and new-origin CORS authorization must be checked before calling the preview functional; no assumption that a successful static build proves a working account session. The API allows origins explicitly using `PI_ALLOWED_ORIGINS` and must admit this preview's exact HTTPS origin, without wildcard or weakening auth.

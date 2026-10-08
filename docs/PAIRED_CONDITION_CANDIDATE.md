# Paired condition candidate and test app

The corrected before/after workbook supplies independent listing evidence. TARGET means dated, original or partially updated; NOT TARGET means fully updated. Source roles and sale uplift never assign condition labels. Automated labels are silver, and uncertainty stays unknown.

## Frozen dataset and training

Dataset `paired-condition-automated-silver` v1 preserves 332 groups / 578 event records / 7,952 stored photos. Prior human corrections are copied into a new version by the narrow private lifecycle function. Original frozen snapshots, splits, processed images and existing production models remain unchanged. Any ambiguous source event quarantines its physical group from initial supervised fitting. Shared MLS keys or usable interior photo hashes additionally keep linked aliases in the most protected split.

Separate regularized heads learn from native pinned SigLIP2 vectors, locally encoded sanitized remarks, and explicit structured fields with missingness. A photo head provides individual photo results. A grouped out-of-fold fusion head ranks overall target strength. Vocabulary, scaling and heads fit training groups only. Image or metadata TARGET may qualify overall TARGET independently. UNKNOWN is never a negative label. Scores are uncalibrated match strengths, not target probabilities or deal quality.

Year built, size, beds/baths, type, systems and condition fields can enter structured training. Prices, DOM, roles, identities, exact geography, sale outcomes and prior rule scores do not. Closed payloads are not verified acquisition-time snapshots. Price-drop and raw MLS information remain visible as context. Comparables and economics belong to a separate later stage.

Model bundles are immutable hash-verified JSON in private existing Storage; no pickle loading, OpenAI calls, private model egress, MLS refresh, production promotion or new service. Existing feature caches are reused; only missing native vectors and changed sanitized text are encoded locally. Retention/revocation guards apply to cached photos. Excluded floor plans, amenities, artificial/staged and unrelated photos do not enter visual aggregation. Automated staging flags are fallible; users can exclude individual photos.

## Development operation

Set `STUDIO_PAIRED_CONDITION_ENABLED=true` on the existing development worker. This isolated lane precedes the old score batch and legacy queue; it claims only this new training/inference request type. The web UI uses only hosted dependencies. Set the existing owner web role to operator to expose explicitly requested training.

Authenticated `/model-test` (also `/`) shows the overall property list, prior/last tabs, photos, raw OData JSON, metadata and remarks, trained per-photo and listing results, and editable photo/metadata/overall decisions. Run model reuses stored data. Save / Save and next appends evidence-bound human corrections with optimistic concurrency. Train next version explicitly freezes corrections and queues a new candidate; saving never retrains automatically. The original `/paired-review` remains available.

Release schema `actvision-paired-condition-v1` deliberately differs from the older C1-C6 v2 schema. This dataset does not supply those labels, and the production v2 adapter must not consume this research candidate. Independent human accuracy and protected-slice approval remain false. Validation/test reports measure silver agreement only.

## Validation

26 focused model, hosted auth/API and worker checks passed, covering split isolation, holdout-label invariance, exclusion of sale/rule features, missing modalities, private access stripping, metadata-only targets, explicit training, CSRF/roles and worker lane isolation. New hosted modules import with all ML imports blocked; Python compilation and browser-script syntax checks pass. Existing broad legacy runtime tests contain pre-existing redirect/acquisition metadata and heavy-import failures outside this new path. DOM acceptance also passed for rendering, XSS escaping, exclusion removal, CSRF payloads, unsaved edits and save-next. Full visual browser acceptance remains unverified: the local Playwright browser download was unavailable, and the cloud app requires a fresh owner sign-in.

## Verified development Candidate 1 (2026-10-08)

Runtime commit: `02442f19bf88902f519cad3dc8f57b849784331c`; existing web and worker deployments are live. The initial attempt failed before feature preparation because the restricted worker had no hashing-schema access. Migration `20261008142721_paired_condition_frozen_checksum` supplies a workspace-scoped read-only verifier, with PUBLIC revoked, preserving the restricted runtime role. The explicit retry completed at 14:42:27 UTC.

Candidate `c71b5414-dd7c-4c63-9d6f-6be7cda4e7bc`, registry version 1, schema `actvision-paired-condition-v1`, remains candidate / uncalibrated / not production ready. All four component model runs completed; the photo head also trained. Bundle SHA-256: `e55512ba80e26fd08d9fb40243e9dfdc7c329467d64bb22ddd49a62f027b5c02`.

Dataset v2 `fcf2748c-4c70-470b-ba11-3025a92beeea` incorporates existing human corrections while preserving v1. Manifest SHA-256: `41ea64ee9e51f34e726e2f8c849f235ffb40e7adef6dab544dc9abf1511db053`. Frozen splits are unchanged. Effective fitting uses 241 events / 139 groups, validation 52 events / 30 groups, and protected test 149 events / 84 groups. Two validation events linked to protected aliases move to test for research fitting. The 136 ambiguous events remain excluded references.

All 578 frozen events have completed saved predictions; 4,210 usable photos have trained per-photo results. The other stored photos remain preserved and excluded from visual scoring as appropriate. There are 126 metadata-driven TARGET results where images did not classify TARGET, confirming independent modality support. All frozen evidence IDs match the evidence rendered by the app.

Held-out silver agreement among non-abstained known labels: image 56/60, metadata 45/55 (15 abstentions), overall 60/61 (6 abstentions). These are label-imitation measurements, not independent accuracy. Validation overall has very limited negative support; no calibration or production quality claim is justified.

The separate inference queue was also verified on saved evidence outside Dataset v2. Evidence `45df86af-f5a7-4d39-8f93-d5305e2cec86` completed at 14:45:04 UTC, using the hash-verified candidate bundle. Its two photos were non-interior and excluded; remarks produced TARGET, structured facts remained uncertain, and the overall result remained TARGET. This confirms inference can score stored properties outside the reference set without MLS or external model calls. This adds one prediction beyond the 578 frozen-event predictions and does not change any dataset or training labels.

The current dev app is https://acq-vision-studio-dev.onrender.com (the root opens the new model testing page after existing owner sign-in). Full cloud visual QA could not proceed without owner sign-in; no authentication was bypassed or weakened. HTTP health identifies the verified runtime commit, all candidate artifacts and predictions are saved, the isolated worker is ready, and 26 focused Python checks plus DOM acceptance passed. No new security advisory warnings appeared; the pre-existing migration-receipts deny-all RLS notice remains unchanged.

Optional UI acceptance: install `jsdom` for `node test_paired_condition_dom.cjs`, or Playwright plus its Chromium browser for `node test_paired_condition_ui.cjs`. Python focused checks: `python -m pytest -q test_paired_condition_models.py test_paired_condition_api.py test_hosted_server.py test_hosted_label_worker.py`.

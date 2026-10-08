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

26 focused model, hosted auth/API and worker checks passed, covering split isolation, holdout-label invariance, exclusion of sale/rule features, missing modalities, private access stripping, metadata-only targets, explicit training, CSRF/roles and worker lane isolation. New hosted modules import with all ML imports blocked; Python compilation and browser-script syntax checks pass. Existing broad legacy runtime tests contain pre-existing redirect/acquisition metadata and heavy-import failures outside this new path. Browser UI acceptance requires a browser; the local Playwright browser download was unavailable in this workspace.

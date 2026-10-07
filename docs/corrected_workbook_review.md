# Corrected prior/last MLS review

The corrected workbook maps two possible marketing events to each physical property. Prior-sale and later-sale evidence are separate candidates. Neither role implies a confirmed TARGET or NOT TARGET label. The simple paired screen retains independent photo, metadata, and overall decisions and optional condition/modernization labels.

## Reuse and ownership

| Logical entity | Storage | Reason |
|---|---|---|
| Physical property and split guard | Existing `property_groups` | Preserve group IDs and all historical split protection. The current import has unique APNs; `source_rows.normalized_unit` retains unit identity. Shared-APN units require explicit group mapping before another import. |
| Import and original rows | New `source_imports`, `source_rows` | Earlier imports used a different source schema. Store each corrected row immutably, including original and audit fields. Join sheets by APN/unit, not their row order. |
| Property/source links | `source_rows.group_id` | Existing groups can have many source rows; a second link table would duplicate this relationship. |
| MLS snapshots | New `listing_events` | Existing examples mix source rows, chosen matches, and target labels. Immutable event versions separate those concerns without overwriting examples. |
| Sale transactions and links | New `sale_transactions`, `transaction_listing_links` | A transaction can have expired, withdrawn, supporting, or conflicting marketing listings. Closing status alone does not select the acquisition. |
| Candidate/human roles | New append-only `property_event_roles` | Preserve candidate reasoning and later human corrections. |
| Feature evidence | New `evidence_snapshots` | Bind structured/text evidence and exact-listing photo manifests to a stable fingerprint. |
| Photo bytes and hashes | Existing `photos`, storage, media documents | Reuse exact listing/group references. No new downloads, provider calls, or re-encoding. |
| Signals and labels | New append-only `remark_signal_events`, `property_label_events` | The existing review tables lack a separate prior/resale evidence key. Keep them intact; show earlier same-MLS reviews as previous evidence rather than silently relabeling a new snapshot. |
| Outcomes | New `flip_outcomes` | Unreviewed source pairs are research only. Gross uplift is not profit. Missing resale is not negative. |
| Datasets/models/releases | Existing tables | Corrected acquisition membership is a draft. No freeze, training, release, or promotion occurs on import. |

## Operator flow

1. Record a backup receipt with schema, migration inventory, counts, reviews, dataset records, and photo/storage manifests in the training workspace. This is an application inventory snapshot, not a substitute for a managed database disaster-recovery backup.
2. Review/apply `sql/paired_workbook_schema.sql` in the training project only.
3. Export existing listing snapshots read-only. Supply JSON arrays with `raw_payload`, `updated_at`, and optional `listing_key`. Preserve conflicting versions; never fetch MLS data merely to import.
4. Run `paired_workbook_import.py --workbook <path> --stored-evidence <json...> --output <private-json>` to inspect deterministic preparation. Keep all workbook/evidence outputs out of Git.
5. Run `paired_workbook_apply.py` without `--apply` first. Applying requires `ACQ_TRAINING_DATABASE_URL` and an explicit matching project ref. It applies bound staging batches and reconciles source counts; it does not apply DDL.
6. Apply the reviewed forward correction and draft membership/outcome scripts only to the training database. Another source import requires a new dataset version and explicit source selection; do not mix imports into an existing draft.
7. Use `/paired-review` after an approved development deployment. Existing `/target-review` remains unchanged by this patch. Sign-in, same-origin checks, review tokens, private image delivery, and workspace roles are reused.

The current SQL outcome research script records a fixed observation cutoff. Change that cutoff deliberately for a new research version, never silently on a rerun.

## Review and feature policy

- Prior: editable TARGET suggestion. Last: editable NOT TARGET suggestion. Missing candidate: UNKNOWN, not a scored negative.
- Photo and metadata decisions are independent. TARGET strength is a human 50–100 scale, not a model probability.
- Verify event role, then save the two available listing reviews and advance. Condition and modernization are optional during general review but mandatory before protected quality claims.
- Manually exclude floor plans, virtual/AI staging, shared amenities, and unrelated photos. If all photos are excluded, visual evidence is insufficient.
- Private snippets remain restricted. Private text signal confirmations are optional, separate append-only judgments. Unreviewed legal/permit/access/financing/defect signals remain drafts and cannot become authoritative training truth.
- Quarantine address/unit/APN/date conflicts, shared listings across transactions, and unclear era assignments. An expired acquisition listing can be relevant and remains reviewable.
- Feature snapshots exclude close prices/dates, contract outcomes, IDs, exact addresses, owner/agent/contact fields, and all later-sale features. Snapshot availability does not establish point-in-time correctness or photo era.
- All events, images, reviews, and outcomes from one physical property stay in the same split. Existing test groups remain held out, even if this increases the test allocation beyond an approximate requested size.

## Component cards (planned, untrained)

| Component | Inputs | Output | Limitation |
|---|---|---|---|
| Structured reference | Reviewed acquisition-era fields and missingness | Neighbor similarity | Snapshot dates need verification. |
| Public text | Acquisition public remarks with quoted spans | Neighbor similarity | Marketing, negation, and renovation-era ambiguity require review. |
| Private signals | Sanitized, individually reviewed typed flags | Neighbor similarity | Missing/unreviewed flags are UNKNOWN. Raw private embeddings are excluded. |
| Vision | Frozen, explicitly pinned SigLIP2 encoder and accepted acquisition photos | Photo/property reference similarity | Model/processor pin, context exclusions, room coverage, and era review must precede evaluation. |
| Fusion | Component similarities plus modality availability | Combined reference similarity | No target probability, profit, or canonical opportunity ranking. |

No component was fitted or evaluated by this import patch. Model cards with actual artifact IDs and measured performance require a reviewed frozen dataset and separate approved runs.

## Forward fix and rollback

Do not delete staging evidence or mutate frozen historical datasets. Keep the existing live UI while this patch is reviewed. To roll back a later deployment, return the dev service to its recorded previous commit; all existing routes/data remain available. For source corrections, append a new workbook/import and role/evidence versions, create a new draft dataset version, and record which import supersedes which. Never reuse old photos or human labels solely because an address matches.

## Validation

Run `python tests/test_paired_review.py`, Python compilation, and JavaScript syntax checking. Before freeze, verify complete row/role counts, Sundance mapping, literal-None handling, maximum date gaps, transaction conflicts, exact-listing photo provenance, private workspace grants, hash consistency, source-only unknown behavior, no outcome leakage, and stable grouped split guards. Repeat representative import batches, including shared-listing and missing-address cases, and require unchanged counts on the second repeat.

Full signed-in browser acceptance, byte rehashing of stored photos, human protected review, freeze approval, the unseen-MLS challenge, hard-negative classification, and reviewed outcome evaluation are later gates. The user explicitly instructed: “Do not merge, deploy, enable model flags, or promote a release automatically.”

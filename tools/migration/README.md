# Administrative migration tools

These preserve the existing pilot migration utilities. They are **not** a hosted
database adapter, a deployment command, or a local cleanup tool.

Set explicit environment configuration before running:

- `ACQ_DATA_ROOT`: existing private pilot dataset/artifact directory.
- `ACQ_EVIDENCE_ROOT`: original record-evidence directory.
- `ACQ_REPORT_ROOT`: optional original reports directory.
- `SUPABASE_PROJECT_REF`: approved destination project reference.
- `SUPABASE_DB_HOST`, optional `SUPABASE_DB_PORT`: destination pooler.
- `PGPASSWORD`: database administration password supplied through the environment.
- `SUPABASE_MIGRATION_STORAGE_KEY`: destination secret key, never a publishable key.

The Storage key can alternatively be in the private data directory's
`.env.migration`. That file is explicitly excluded from backup preparation.
Install this directory's `requirements.txt` if using these tools.

`migrate_database.py validate` uses rollback validation; `migrate` creates private
training tables and imports the finished historical cohort. The initial importer
expects the original cohort's frozen shape/counts. Review and adapt its assertions
before using it for a different dataset. Do not use this against your production
MLS project. Later review/provenance changes need a separate delta migration.

`supabase_training_schema.sql` is the target design for new installations. It now
includes photo-context/property-priority review events, dataset purposes, protected
evaluation slices, component model releases, and image/metadata/fusion prediction
modes. It is still a draft, not an idempotent delta for an existing deployment.
For an existing Supabase project, compare the live schema, write a forward-only
migration, validate it in a staging branch, and keep release promotion restricted
to an administrative role. The training worker intentionally cannot approve model
releases.

`../../supabase/migrations/20261001151500_multimodal_training.sql` is the reviewable
forward-only delta for a deployment that already has the original `acq_training`
schema. Validate it in a staging branch and compare every constraint/policy name to
the live project before applying it. It adds no worker permission to approve
releases or edit evaluation slices.

`migrate_storage.py prepare` inventories files; `transfer` uploads and downloads
objects again to verify hashes. It rejects a changed manifest when resuming.
`verify_restore.py` restores sample artifacts to temporary files and checks hashes.
Private restore receipts remain under the configured data directory.

The dataset may contain legacy absolute paths. A backup is not a working remote
cutover. Never delete originals until restoration, all object references and the
new running application have been verified.

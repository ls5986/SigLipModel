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

`migrate_storage.py prepare` inventories files; `transfer` uploads and downloads
objects again to verify hashes. It rejects a changed manifest when resuming.
`verify_restore.py` restores sample artifacts to temporary files and checks hashes.
Private restore receipts remain under the configured data directory.

The dataset may contain legacy absolute paths. A backup is not a working remote
cutover. Never delete originals until restoration, all object references and the
new running application have been verified.

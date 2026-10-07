-- Additive provenance for workbook-driven review. Run once through migration tooling.
ALTER TABLE acq_training.property_target_review
 ADD COLUMN normalized_apn text,
 ADD COLUMN source_import jsonb NOT NULL DEFAULT '{}'::jsonb,
 ADD COLUMN cohort_status text NOT NULL DEFAULT 'LEGACY',
 ADD COLUMN active boolean NOT NULL DEFAULT true;
CREATE TABLE acq_training.review_import_rows (
 workspace_id uuid NOT NULL,
 import_sha256 text NOT NULL CHECK(length(import_sha256)=64),
 normalized_apn text NOT NULL,
 payload jsonb NOT NULL,
 imported_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(workspace_id,import_sha256,normalized_apn)
);
ALTER TABLE acq_training.review_import_rows ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON acq_training.review_import_rows FROM PUBLIC,anon,authenticated;
GRANT SELECT ON acq_training.review_import_rows TO acq_training_reader,acq_training_reviewer;
CREATE POLICY import_read ON acq_training.review_import_rows FOR SELECT
 TO acq_training_reader,acq_training_reviewer
 USING(acq_training.allowed_workspace(workspace_id));

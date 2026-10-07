-- Additive review sheet. Existing examples, datasets, labels and photos remain untouched.
CREATE TABLE acq_training.property_target_review (
 workspace_id uuid NOT NULL,
 group_id uuid NOT NULL,
 dataset_id uuid NOT NULL,
 listing_key text NOT NULL,
 evidence_identity text NOT NULL,
 event_snapshot jsonb NOT NULL,
 odata_json jsonb NOT NULL,
 image_decision text CHECK (image_decision IN ('TARGET','NOT_TARGET','INSUFFICIENT_EVIDENCE')),
 image_strength integer,
 metadata_decision text CHECK (metadata_decision IN ('TARGET','NOT_TARGET','INSUFFICIENT_EVIDENCE')),
 metadata_strength integer,
 overall_decision text NOT NULL DEFAULT 'TARGET' CHECK (overall_decision IN ('TARGET','NOT_TARGET')),
 notes text NOT NULL DEFAULT '' CHECK (length(notes)<=4000),
 revision bigint NOT NULL DEFAULT 0 CHECK (revision>=0),
 reviewed_by text,
 reviewed_at timestamptz,
 frozen_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(workspace_id,group_id),
 CHECK ((image_decision='TARGET' AND image_strength BETWEEN 50 AND 100 AND image_strength IS NOT NULL)
   OR (image_decision IS DISTINCT FROM 'TARGET' AND image_strength IS NULL)),
 CHECK ((metadata_decision='TARGET' AND metadata_strength BETWEEN 50 AND 100 AND metadata_strength IS NOT NULL)
   OR (metadata_decision IS DISTINCT FROM 'TARGET' AND metadata_strength IS NULL))
);
ALTER TABLE acq_training.property_target_review ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON acq_training.property_target_review FROM PUBLIC,anon,authenticated;
GRANT SELECT ON acq_training.property_target_review TO acq_training_reader,acq_training_reviewer;
GRANT UPDATE(image_decision,image_strength,metadata_decision,metadata_strength,overall_decision,
 notes,revision,reviewed_by,reviewed_at) ON acq_training.property_target_review TO acq_training_reviewer;
CREATE POLICY review_read ON acq_training.property_target_review FOR SELECT
 TO acq_training_reader,acq_training_reviewer USING(acq_training.allowed_workspace(workspace_id));
CREATE POLICY review_save ON acq_training.property_target_review FOR UPDATE
 TO acq_training_reviewer USING(acq_training.allowed_workspace(workspace_id))
 WITH CHECK(acq_training.allowed_workspace(workspace_id));

INSERT INTO acq_training.property_target_review
 (workspace_id,group_id,dataset_id,listing_key,evidence_identity,event_snapshot,odata_json)
SELECT i.workspace_id,i.group_id,i.dataset_id,event->>'property_id',event->>'evidence_id',event,
 source.listing
FROM acq_training.dataset_items i
JOIN acq_training.datasets d ON (d.workspace_id,d.id)=(i.workspace_id,i.dataset_id)
CROSS JOIN LATERAL jsonb_array_elements(i.label_snapshot->'events') event
CROSS JOIN LATERAL (
 SELECT candidate->'listing' AS listing
 FROM acq_training.examples e
 CROSS JOIN LATERAL jsonb_array_elements(e.source_snapshot->'mls_candidates') candidate
 WHERE e.workspace_id=i.workspace_id AND e.group_id=i.group_id
 AND candidate->'listing'->>'ListingKey'=event->>'property_id'
 ORDER BY e.id LIMIT 1
) source
WHERE d.name='actvision-v2' AND d.version=1 AND event->>'event_role'='acquisition';
DO $$ BEGIN
 IF (SELECT count(*) FROM acq_training.property_target_review)<>415 THEN
  RAISE EXCEPTION 'Expected exactly 415 unique acquisition property groups';
 END IF;
END $$;

-- Repair is scoped by existing workspace RLS and server-only database login.
GRANT UPDATE(listing_key,evidence_identity,event_snapshot,odata_json,frozen_at) ON acq_training.property_target_review TO acq_training_reviewer;

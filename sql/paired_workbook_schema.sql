-- Additive staging only. Existing reviews, groups, photos and frozen datasets remain untouched.
CREATE TABLE acq_training.source_imports (
 workspace_id uuid NOT NULL, id uuid NOT NULL DEFAULT gen_random_uuid(),
 source_name text NOT NULL, source_sha256 text NOT NULL CHECK(length(source_sha256)=64),
 schema_version text NOT NULL, row_count integer NOT NULL CHECK(row_count>0),
 imported_at timestamptz NOT NULL DEFAULT now(), status text NOT NULL CHECK(status IN ('staged','withdrawn','review_ready')),
 PRIMARY KEY(workspace_id,id), UNIQUE(workspace_id,source_sha256)
);
CREATE TABLE acq_training.source_rows (
 workspace_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),source_import_id uuid NOT NULL,
 source_row_number integer NOT NULL,group_id uuid NOT NULL,normalized_apn text NOT NULL,
 normalized_unit text NOT NULL DEFAULT '',address text NOT NULL,raw_snapshot jsonb NOT NULL,
 prior_sale_date date,prior_recording_date date,prior_sale_amount numeric,
 last_sale_date date,last_recording_date date,last_sale_amount numeric,
 created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(workspace_id,id),UNIQUE(workspace_id,source_import_id,source_row_number),
 FOREIGN KEY(workspace_id,source_import_id) REFERENCES acq_training.source_imports(workspace_id,id),
 FOREIGN KEY(workspace_id,group_id) REFERENCES acq_training.property_groups(workspace_id,id)
);
CREATE TABLE acq_training.listing_events (
 workspace_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),group_id uuid NOT NULL,
 provider text NOT NULL,listing_key text NOT NULL,listing_id text,standard_status text,
 raw_snapshot jsonb NOT NULL,snapshot_at timestamptz NOT NULL,evidence_sha256 text NOT NULL CHECK(length(evidence_sha256)=64),
 PRIMARY KEY(workspace_id,id),UNIQUE(workspace_id,group_id,listing_key,evidence_sha256),
 FOREIGN KEY(workspace_id,group_id) REFERENCES acq_training.property_groups(workspace_id,id)
);
CREATE TABLE acq_training.sale_transactions (
 workspace_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),group_id uuid NOT NULL,
 close_date date,recording_date date,close_price numeric,transaction_identity text NOT NULL,
 verification_status text NOT NULL DEFAULT 'machine_candidate',
 PRIMARY KEY(workspace_id,id),UNIQUE(workspace_id,group_id,transaction_identity),
 FOREIGN KEY(workspace_id,group_id) REFERENCES acq_training.property_groups(workspace_id,id)
);
CREATE TABLE acq_training.property_event_roles (
 workspace_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),source_row_id uuid NOT NULL,
 group_id uuid NOT NULL,listing_event_id uuid,transaction_id uuid,event_role text NOT NULL CHECK(event_role IN ('acquisition','later_resale')),
 decision text NOT NULL CHECK(decision IN ('machine_candidate','human_confirmed','wrong_property','wrong_era','ambiguous','source_only','provider_error','revoked')),
 provenance text NOT NULL,reviewer text,reviewed_at timestamptz,revision bigint NOT NULL DEFAULT 0,
 audit jsonb NOT NULL,evidence_sha256 text NOT NULL,created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(workspace_id,id),UNIQUE(workspace_id,source_row_id,event_role,revision),
 FOREIGN KEY(workspace_id,source_row_id) REFERENCES acq_training.source_rows(workspace_id,id),
 FOREIGN KEY(workspace_id,group_id) REFERENCES acq_training.property_groups(workspace_id,id),
 FOREIGN KEY(workspace_id,listing_event_id) REFERENCES acq_training.listing_events(workspace_id,id),
 FOREIGN KEY(workspace_id,transaction_id) REFERENCES acq_training.sale_transactions(workspace_id,id)
);
CREATE TABLE acq_training.transaction_listing_links (
 workspace_id uuid NOT NULL,transaction_id uuid NOT NULL,listing_event_id uuid NOT NULL,
 relationship text NOT NULL CHECK(relationship IN ('primary','duplicate','supporting','conflicting')),
 date_gap_days integer CHECK(date_gap_days>=0),match_method text NOT NULL,review_status text NOT NULL,
 PRIMARY KEY(workspace_id,transaction_id,listing_event_id),
 FOREIGN KEY(workspace_id,transaction_id) REFERENCES acq_training.sale_transactions(workspace_id,id),
 FOREIGN KEY(workspace_id,listing_event_id) REFERENCES acq_training.listing_events(workspace_id,id)
);
CREATE TABLE acq_training.evidence_snapshots (
 workspace_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),listing_event_id uuid NOT NULL,
 group_id uuid NOT NULL,as_of timestamptz NOT NULL,metadata_snapshot jsonb NOT NULL,
 photo_manifest jsonb NOT NULL,available_modalities text[] NOT NULL,feature_policy_version text NOT NULL,
 evidence_sha256 text NOT NULL CHECK(length(evidence_sha256)=64),
 PRIMARY KEY(workspace_id,id),UNIQUE(workspace_id,listing_event_id,evidence_sha256),
 FOREIGN KEY(workspace_id,listing_event_id) REFERENCES acq_training.listing_events(workspace_id,id),
 FOREIGN KEY(workspace_id,group_id) REFERENCES acq_training.property_groups(workspace_id,id)
);
CREATE TABLE acq_training.remark_signal_events (
 workspace_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),evidence_snapshot_id uuid NOT NULL,
 source_field text NOT NULL CHECK(source_field IN ('PublicRemarks','PrivateRemarks')),
 signal_name text NOT NULL,state text NOT NULL CHECK(state IN ('PRESENT','ABSENT','UNKNOWN')),
 snippet text,start_offset integer,end_offset integer,provenance text NOT NULL,
 model_version text NOT NULL,review_status text NOT NULL,reviewer text,created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(workspace_id,id),UNIQUE(workspace_id,evidence_snapshot_id,source_field,signal_name,start_offset),
 FOREIGN KEY(workspace_id,evidence_snapshot_id) REFERENCES acq_training.evidence_snapshots(workspace_id,id),
 CHECK((state='PRESENT' AND snippet IS NOT NULL AND start_offset>=0 AND end_offset>start_offset) OR state IN ('ABSENT','UNKNOWN'))
);
CREATE TABLE acq_training.property_label_events (
 workspace_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),group_id uuid NOT NULL,evidence_snapshot_id uuid NOT NULL,
 label_axis text NOT NULL,label_value text NOT NULL,answer jsonb NOT NULL,
 provenance text NOT NULL,review_status text NOT NULL,reviewer text NOT NULL,
 revision bigint NOT NULL CHECK(revision>0),created_at timestamptz NOT NULL DEFAULT now(),
 PRIMARY KEY(workspace_id,id),UNIQUE(workspace_id,evidence_snapshot_id,label_axis,revision),
 FOREIGN KEY(workspace_id,group_id) REFERENCES acq_training.property_groups(workspace_id,id),
 FOREIGN KEY(workspace_id,evidence_snapshot_id) REFERENCES acq_training.evidence_snapshots(workspace_id,id)
);
CREATE TABLE acq_training.flip_outcomes (
 workspace_id uuid NOT NULL,id uuid NOT NULL DEFAULT gen_random_uuid(),group_id uuid NOT NULL,
 acquisition_transaction_id uuid NOT NULL,resale_transaction_id uuid NOT NULL,
 acquisition_price numeric,resale_price numeric,gross_uplift_dollars numeric,gross_uplift_percent numeric,hold_days integer,
 observation_cutoff date NOT NULL,follow_up_days integer,resale_observed boolean NOT NULL,
 pair_decision text NOT NULL,reviewer text,reviewed_at timestamptz,label_version text NOT NULL,
 PRIMARY KEY(workspace_id,id),UNIQUE(workspace_id,acquisition_transaction_id,resale_transaction_id,label_version),
 FOREIGN KEY(workspace_id,group_id) REFERENCES acq_training.property_groups(workspace_id,id),
 FOREIGN KEY(workspace_id,acquisition_transaction_id) REFERENCES acq_training.sale_transactions(workspace_id,id),
 FOREIGN KEY(workspace_id,resale_transaction_id) REFERENCES acq_training.sale_transactions(workspace_id,id)
);
-- No authenticated/anon grants. Existing server-role workspace boundary is reused.
DO $$ DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['source_imports','source_rows','listing_events','sale_transactions','property_event_roles','transaction_listing_links','evidence_snapshots','remark_signal_events','property_label_events','flip_outcomes'] LOOP
  EXECUTE format('ALTER TABLE acq_training.%I ENABLE ROW LEVEL SECURITY',t);
  EXECUTE format('REVOKE ALL ON acq_training.%I FROM PUBLIC,anon,authenticated',t);
  EXECUTE format('GRANT SELECT ON acq_training.%I TO acq_training_reader,acq_training_reviewer',t);
  EXECUTE format('CREATE POLICY workspace_read ON acq_training.%I FOR SELECT TO acq_training_reader,acq_training_reviewer USING(acq_training.allowed_workspace(workspace_id))',t);
 END LOOP;
END $$;
GRANT INSERT ON acq_training.property_label_events,acq_training.property_event_roles TO acq_training_reviewer;
CREATE POLICY workspace_insert ON acq_training.property_label_events FOR INSERT TO acq_training_reviewer WITH CHECK(acq_training.allowed_workspace(workspace_id));
CREATE POLICY workspace_insert ON acq_training.property_event_roles FOR INSERT TO acq_training_reviewer WITH CHECK(acq_training.allowed_workspace(workspace_id));
-- Immutable provenance/evidence and append-only role/label history, including operator writes.
CREATE FUNCTION acq_training.corrected_import_immutable() RETURNS trigger LANGUAGE plpgsql SET search_path = pg_catalog,acq_training AS $$
 BEGIN RAISE EXCEPTION 'Corrected import evidence is immutable; append a new version'; END $$;
DO $$ DECLARE t text;BEGIN
 FOREACH t IN ARRAY ARRAY['source_imports','source_rows','listing_events','sale_transactions','property_event_roles','transaction_listing_links','evidence_snapshots','remark_signal_events','property_label_events','flip_outcomes'] LOOP
 EXECUTE format('CREATE TRIGGER immutable_evidence BEFORE UPDATE OR DELETE ON acq_training.%I FOR EACH ROW EXECUTE FUNCTION acq_training.corrected_import_immutable()',t);
 END LOOP;
END $$;

-- Live Studio state, separate from immutable migration backups and source evidence.
CREATE TABLE acq_training.studio_state (
  workspace_id uuid NOT NULL,
  kind text NOT NULL CHECK (kind IN ('image','property','era','document')),
  item_id text NOT NULL CHECK (length(item_id) BETWEEN 1 AND 512),
  revision bigint NOT NULL CHECK (revision > 0),
  payload jsonb NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
  updated_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY (workspace_id,kind,item_id)
);
CREATE TABLE acq_training.studio_state_history (
  workspace_id uuid NOT NULL, kind text NOT NULL, item_id text NOT NULL,
  revision bigint NOT NULL, payload jsonb NOT NULL,
  created_at timestamptz NOT NULL DEFAULT now(),
  PRIMARY KEY(workspace_id,kind,item_id,revision)
);
ALTER TABLE acq_training.studio_state ENABLE ROW LEVEL SECURITY;
ALTER TABLE acq_training.studio_state_history ENABLE ROW LEVEL SECURITY;
REVOKE ALL ON acq_training.studio_state, acq_training.studio_state_history FROM PUBLIC,anon,authenticated;
GRANT SELECT ON acq_training.studio_state, acq_training.studio_state_history TO acq_training_reader;
GRANT SELECT,INSERT,UPDATE ON acq_training.studio_state TO acq_training_reviewer,acq_training_worker;
GRANT SELECT,INSERT ON acq_training.studio_state_history TO acq_training_reviewer,acq_training_worker;
CREATE POLICY workspace_access ON acq_training.studio_state
 TO acq_training_reader,acq_training_reviewer,acq_training_worker
 USING (acq_training.allowed_workspace(workspace_id))
 WITH CHECK (acq_training.allowed_workspace(workspace_id));
CREATE POLICY workspace_access ON acq_training.studio_state_history
 TO acq_training_reader,acq_training_reviewer,acq_training_worker
 USING (acq_training.allowed_workspace(workspace_id))
 WITH CHECK (acq_training.allowed_workspace(workspace_id));
-- Restricted backend roles may read legacy labels; never grant API roles access.
GRANT SELECT ON acq_training.migration_documents TO acq_training_reader,acq_training_reviewer,acq_training_worker;
CREATE POLICY studio_workspace_read ON acq_training.migration_documents FOR SELECT
 TO acq_training_reader,acq_training_reviewer,acq_training_worker
 USING (acq_training.allowed_workspace(workspace_id));
CREATE FUNCTION acq_training.save_studio_state(w uuid, k text, i text, expected bigint, body jsonb)
RETURNS bigint LANGUAGE plpgsql SECURITY INVOKER SET search_path='' AS $$
DECLARE next_revision bigint;
BEGIN
 IF expected < 0 OR expected IS NULL THEN RAISE EXCEPTION 'Invalid revision' USING ERRCODE='22023'; END IF;
 INSERT INTO acq_training.studio_state(workspace_id,kind,item_id,revision,payload)
 SELECT w,k,i,1,body WHERE expected=0
 ON CONFLICT DO NOTHING RETURNING revision INTO next_revision;
 IF next_revision IS NULL THEN
  UPDATE acq_training.studio_state SET revision=revision+1,payload=body,updated_at=now()
  WHERE workspace_id=w AND kind=k AND item_id=i AND revision=expected
  RETURNING revision INTO next_revision;
 END IF;
 IF next_revision IS NULL THEN RAISE EXCEPTION 'Review changed; reload before saving' USING ERRCODE='40001'; END IF;
 INSERT INTO acq_training.studio_state_history(workspace_id,kind,item_id,revision,payload)
 VALUES(w,k,i,next_revision,body);
 RETURN next_revision;
END $$;
REVOKE ALL ON FUNCTION acq_training.save_studio_state(uuid,text,text,bigint,jsonb) FROM PUBLIC,anon,authenticated;
GRANT EXECUTE ON FUNCTION acq_training.save_studio_state(uuid,text,text,bigint,jsonb) TO acq_training_reviewer,acq_training_worker;

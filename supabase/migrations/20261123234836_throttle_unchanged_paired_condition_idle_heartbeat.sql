CREATE OR REPLACE FUNCTION acq_training.save_studio_state(w uuid, k text, i text, expected bigint, body jsonb)
RETURNS bigint LANGUAGE plpgsql SECURITY INVOKER SET search_path TO '' AS $function$
DECLARE next_revision bigint; previous_body jsonb; previous_updated_at timestamptz; skip_idle_history boolean := false;
BEGIN
 IF expected < 0 OR expected IS NULL THEN RAISE EXCEPTION 'Invalid revision' USING ERRCODE='22023'; END IF;
 IF k='document' AND i='paired-condition-worker' AND expected>0 AND body-'at'='{"policy":"paired-condition-heads-v1","status":"ready"}'::jsonb THEN
  SELECT payload,updated_at INTO previous_body,previous_updated_at FROM acq_training.studio_state WHERE workspace_id=w AND kind=k AND item_id=i AND revision=expected;
  skip_idle_history := COALESCE(previous_body-'at'=body-'at',false);
  -- Only the exact unchanged idle heartbeat may return without a write.
  -- A revision mismatch or any state transition retains original CAS semantics.
  IF skip_idle_history AND previous_updated_at > now() - interval '60 seconds' THEN
   RETURN expected;
  END IF;
 END IF;
 INSERT INTO acq_training.studio_state(workspace_id,kind,item_id,revision,payload)
 SELECT w,k,i,1,body WHERE expected=0
 ON CONFLICT DO NOTHING RETURNING revision INTO next_revision;
 IF next_revision IS NULL THEN
  UPDATE acq_training.studio_state SET revision=revision+1,payload=body,updated_at=now()
  WHERE workspace_id=w AND kind=k AND item_id=i AND revision=expected
  RETURNING revision INTO next_revision;
 END IF;
 IF next_revision IS NULL THEN RAISE EXCEPTION 'Review changed; reload before saving' USING ERRCODE='40001'; END IF;
 IF NOT skip_idle_history THEN
  INSERT INTO acq_training.studio_state_history(workspace_id,kind,item_id,revision,payload)
  VALUES(w,k,i,next_revision,body);
 END IF;
 RETURN next_revision;
END $function$;

-- Applied to training Supabase 2026-10-08. Append-only dataset version; rerun refuses overwrite.

do $freeze$
declare
 b record; entry jsonb; ev record; listing record; source record; score jsonb;
 v_dataset_id uuid; v_example_id uuid; labels jsonb; hashes text[]; policy jsonb; manifest jsonb; bad_group boolean;
begin
 select * into strict b from acq_training.studio_state
 where kind='document' and item_id='paired-auto-batch:paired-auto-20261008-v1' for update;
 if b.payload->>'status'<>'scored' or b.payload->>'freeze_authorized'<>'true' then raise exception 'Batch not complete or authorized'; end if;
 if jsonb_array_length(b.payload->'items')<>578 then raise exception 'Unexpected cohort'; end if;
 if exists(select 1 from acq_training.datasets where workspace_id=b.workspace_id and name='paired-condition-automated-silver' and version=1) then
  raise exception 'Dataset version already exists; inspect rather than overwrite';
 end if;
 policy:=jsonb_build_object(
  'batch_id',b.payload->>'batch_id','source_sha256',b.payload->>'source_sha256',
  'policy',b.payload->>'policy','provenance','AUTOMATED_SILVER','is_gold',false,
  'training_started',false,'training_authorized',false,'human_review_required',false,
  'backbone_revision',b.payload->>'backbone_revision','text_checkpoint',b.payload->'text_checkpoint',
  'model',b.payload->>'model','private_data_external_model_egress',false,
  'selection','all 246 available pairs plus all 86 before-only groups; requested top 250 pairs capped by available count',
  'unknown_policy','INSUFFICIENT_EVIDENCE is never a negative label; omit that axis from supervised loss',
  'split_policy','Pinned property groups; all historical protected test memberships retained',
  'condition_definition','TARGET includes dated/original/partly updated; NOT_TARGET indicates fully updated prompt or remark evidence',
  'model_inputs','Photos, condition metadata, public remarks and available private remarks. No event role, workbook target, delta, close price/date, resale outcome, identifiers or selection audit as condition features',
  'price_features','Snapshot prices and DOM are preserved; acquisition-time availability unverified. Exclude from acquisition-time validation until reconstructed',
  'quarantine_policy','Entire groups with ambiguous candidates, chronology conflicts or shared-listing ambiguity excluded from initial supervised training',
  'feature_cache_policy','Embedding cache is optional and recomputable from frozen photo hashes and pinned checkpoints; two early records lack cached image embeddings',
  'loader_contract','Requires paired-event loader using label_snapshot.frozen_evidence and photo_labels; do not use legacy reference_label as truth',
  'manifest_serialization','UTF-8 PostgreSQL jsonb::text, SHA-256; ordered group/item summaries with SHA-256 of each full label snapshot',
  'staging_detection','Heuristic; not verified AI image detection',
  'scores_are_probabilities',false
 );
 insert into acq_training.datasets(workspace_id,name,version,label_policy,purpose)
 values(b.workspace_id,'paired-condition-automated-silver',1,policy,'training') returning id into v_dataset_id;
 insert into acq_training.dataset_groups(workspace_id,dataset_id,group_id,split)
 select b.workspace_id,v_dataset_id,(i->>'group_id')::uuid,min(i->>'split')
 from jsonb_array_elements(b.payload->'items') i group by i->>'group_id'
 having count(distinct i->>'split')=1;
 if (select count(*) from acq_training.dataset_groups d where d.workspace_id=b.workspace_id and d.dataset_id=v_dataset_id)<>332 then raise exception 'Group/split mismatch'; end if;
 for entry in select value from jsonb_array_elements(b.payload->'items') loop
  select * into strict ev from acq_training.evidence_snapshots
   where workspace_id=b.workspace_id and id=(entry->>'evidence_id')::uuid;
  if ev.evidence_sha256<>entry->>'evidence_sha256' then raise exception 'Pinned evidence changed'; end if;
  select * into strict listing from acq_training.listing_events where workspace_id=b.workspace_id and id=(entry->>'listing_event_id')::uuid;
  select * into strict source from acq_training.source_rows where workspace_id=b.workspace_id and id=(entry->>'source_row_id')::uuid;
  select payload into strict score from acq_training.studio_state where workspace_id=b.workspace_id and kind='document'
   and item_id='paired-auto-score:'||(b.payload->>'batch_id')||':'||(entry->>'evidence_id');
  if score->>'status'<>'completed' or score->>'evidence_sha256'<>ev.evidence_sha256 or
   (score->>'photo_count_scored')::int<>jsonb_array_length(ev.photo_manifest) or jsonb_array_length(score->'unavailable_photos')<>0
  then raise exception 'Incomplete or inconsistent score'; end if;
  select exists(select 1 from jsonb_array_elements(b.payload->'items') i where i->>'group_id'=entry->>'group_id'
   and (i->>'candidate_decision'='ambiguous' or i->>'pair_decision' in ('chronology_conflict','shared_listing_ambiguous'))) into bad_group;
  select coalesce(array_agg(p->>'sha256' order by n),'{}'::text[]) into hashes
   from jsonb_array_elements(ev.photo_manifest) with ordinality as t(p,n);
  labels:=(score-'photos'-'remarks_semantic')||jsonb_build_object(
   'photo_labels',(select coalesce(jsonb_agg(p-'image_embedding' order by n),'[]'::jsonb)
      from jsonb_array_elements(score->'photos') with ordinality as t(p,n)),
   'remarks_encoding',(score->'remarks_semantic')-'features',
   'frozen_evidence',jsonb_build_object('metadata',ev.metadata_snapshot,'private_remarks',listing.raw_snapshot->'PrivateRemarks',
      'raw_listing_snapshot',listing.raw_snapshot,'photo_manifest',ev.photo_manifest,'as_of',ev.as_of,'feature_policy',ev.feature_policy_version),
   'selection_provenance',entry,
   'research_training_eligible',not bad_group,
   'image_axis_eligible',not bad_group and score->'image'->>'decision' in ('TARGET','NOT_TARGET'),
   'metadata_axis_eligible',not bad_group and score->'metadata'->>'decision' in ('TARGET','NOT_TARGET'),
   'feature_cache_document','paired-auto-score:'||(b.payload->>'batch_id')||':'||(entry->>'evidence_id')
  );
  insert into acq_training.examples(workspace_id,group_id,provider,listing_key,listing_id,
   source_workbook_sha256,source_rows,target_transaction,source_snapshot,snapshot_at,
   match_status,photo_era,reference_label,reference_origin,reference_is_gold,available_modalities)
  values(b.workspace_id,(entry->>'group_id')::uuid,listing.provider,listing.listing_key,listing.listing_id,
   b.payload->>'source_sha256',array[source.source_row_number],'{}'::jsonb,listing.raw_snapshot,listing.snapshot_at,
   'candidate',case when entry->>'event_role'='later_resale' then 'later_resale' else 'acquisition_candidate' end,
   null,'PAIRED_AUTOMATED_SILVER:'||(b.payload->>'batch_id'),false,ev.available_modalities) returning id into v_example_id;
  insert into acq_training.dataset_items(workspace_id,dataset_id,group_id,example_id,label_snapshot,photo_hashes,source_review_ids)
   values(b.workspace_id,v_dataset_id,(entry->>'group_id')::uuid,v_example_id,labels,hashes,'{}'::uuid[]);
 end loop;
 if (select count(*) from acq_training.dataset_items d where d.workspace_id=b.workspace_id and d.dataset_id=v_dataset_id)<>578 then raise exception 'Incomplete dataset'; end if;
 select jsonb_build_object('policy',policy,
  'groups',(select jsonb_agg(jsonb_build_object('group_id',d.group_id,'split',d.split) order by d.group_id)
   from acq_training.dataset_groups d where d.workspace_id=b.workspace_id and d.dataset_id=v_dataset_id),
  'items',(select jsonb_agg(jsonb_build_object('example_id',d.example_id,'group_id',d.group_id,
    'photo_hashes',d.photo_hashes,'label_sha256',encode(extensions.digest(convert_to(d.label_snapshot::text,'UTF8'),'sha256'),'hex'))
    order by d.example_id) from acq_training.dataset_items d where d.workspace_id=b.workspace_id and d.dataset_id=v_dataset_id)
 ) into manifest;
 update acq_training.datasets d set manifest_sha256=encode(extensions.digest(convert_to(manifest::text,'UTF8'),'sha256'),'hex'),frozen_at=now()
  where d.workspace_id=b.workspace_id and d.id=v_dataset_id;
 update acq_training.studio_state set revision=revision+1,updated_at=now(),
  payload=payload||jsonb_build_object('freeze_pending',false,'frozen_dataset_id',v_dataset_id,
   'frozen_dataset_name','paired-condition-automated-silver','frozen_dataset_version',1,'frozen_at',now())
  where workspace_id=b.workspace_id and kind='document' and item_id=b.item_id;
end $freeze$;
select name,version,frozen_at,manifest_sha256,id from acq_training.datasets
where name='paired-condition-automated-silver' and version=1;
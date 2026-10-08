-- Narrow private lifecycle functions; no new tables or broad runtime grants.
begin;

create or replace function acq_training.freeze_paired_condition_next(
 candidate_workspace uuid, parent_dataset uuid, actor text
) returns uuid language plpgsql security definer set search_path='' as $$
declare parent_row record; child_id uuid; next_version integer; item record;
 labels jsonb; review record; current_revision bigint; exclusions jsonb; policy jsonb; manifest jsonb;
begin
 if not acq_training.allowed_workspace(candidate_workspace) then raise exception 'Workspace is not authorized'; end if;
 if actor is null or length(actor)<1 or length(actor)>200 then raise exception 'Reviewer identity is required'; end if;
 perform pg_advisory_xact_lock(hashtextextended(candidate_workspace::text||'paired-condition-dataset',0));
 select * into strict parent_row from acq_training.datasets where workspace_id=candidate_workspace
  and id=parent_dataset and name='paired-condition-automated-silver' and frozen_at is not null;
 select jsonb_build_object('policy',parent_row.label_policy,
  'groups',(select jsonb_agg(jsonb_build_object('group_id',g.group_id,'split',g.split) order by g.group_id) from acq_training.dataset_groups g where g.workspace_id=candidate_workspace and g.dataset_id=parent_dataset),
  'items',(select jsonb_agg(jsonb_build_object('example_id',i.example_id,'group_id',i.group_id,'photo_hashes',i.photo_hashes,'label_sha256',encode(extensions.digest(convert_to(i.label_snapshot::text,'UTF8'),'sha256'),'hex')) order by i.example_id) from acq_training.dataset_items i where i.workspace_id=candidate_workspace and i.dataset_id=parent_dataset)) into manifest;
 if encode(extensions.digest(convert_to(manifest::text,'UTF8'),'sha256'),'hex')<>parent_row.manifest_sha256 then raise exception 'Parent dataset checksum differs'; end if;
 if not exists(
  select 1 from acq_training.dataset_items i join acq_training.property_label_events e
   on e.workspace_id=i.workspace_id and e.evidence_snapshot_id=(i.label_snapshot->>'evidence_id')::uuid
  where i.workspace_id=candidate_workspace and i.dataset_id=parent_dataset and e.review_status='approved'
   and e.provenance='HUMAN_REVIEWED' and e.revision>coalesce((i.label_snapshot->>'review_revision')::bigint,0)
   and e.label_axis in('image','metadata','overall','photo_exclusions')
 ) then return parent_dataset; end if;
 select coalesce(max(version),0)+1 into next_version from acq_training.datasets
  where workspace_id=candidate_workspace and name=parent_row.name;
 policy:=parent_row.label_policy||jsonb_build_object('parent_dataset_id',parent_dataset,
  'parent_manifest_sha256',parent_row.manifest_sha256,'corrections_frozen_by',actor,
  'human_correction_policy','Approved condition corrections override only their axes; no source/era confirmation implied',
  'training_authorized',true);
 insert into acq_training.datasets(workspace_id,name,version,label_policy,purpose)
 values(candidate_workspace,parent_row.name,next_version,policy,'training') returning id into child_id;
 insert into acq_training.dataset_groups(workspace_id,dataset_id,group_id,split)
 select workspace_id,child_id,group_id,split from acq_training.dataset_groups
 where workspace_id=candidate_workspace and dataset_id=parent_dataset;
 for item in select * from acq_training.dataset_items where workspace_id=candidate_workspace and dataset_id=parent_dataset loop
  labels:=item.label_snapshot; current_revision:=coalesce((labels->>'review_revision')::bigint,0); exclusions:='{}'::jsonb;
  for review in select distinct on(label_axis) id,label_axis,answer,revision from acq_training.property_label_events
   where workspace_id=candidate_workspace and evidence_snapshot_id=(labels->>'evidence_id')::uuid
    and provenance='HUMAN_REVIEWED' and review_status='approved'
    and label_axis in('image','metadata','overall','notes','photo_exclusions')
   order by label_axis,revision desc loop
   if review.label_axis in('image','metadata','overall') then
    if review.answer->>'decision' not in('TARGET','NOT_TARGET','INSUFFICIENT_EVIDENCE') then
     raise exception 'Invalid paired condition correction'; end if;
    labels:=jsonb_set(labels,array[review.label_axis],review.answer);
   elsif review.label_axis='photo_exclusions' then
    exclusions:=coalesce(review.answer->'excluded','{}'::jsonb);
   elsif review.label_axis='notes' then
    labels:=jsonb_set(labels,'{review_notes}',review.answer);
   end if;
   current_revision:=greatest(current_revision,review.revision);
  end loop;
  if current_revision>coalesce((item.label_snapshot->>'review_revision')::bigint,0) then
   labels:=labels||jsonb_build_object('review_revision',current_revision,'correction_provenance','HUMAN_REVIEWED',
     'is_gold',false,'image_axis_eligible',coalesce((labels->>'research_training_eligible')::boolean,false)
       and labels->'image'->>'decision' in('TARGET','NOT_TARGET'),
     'metadata_axis_eligible',coalesce((labels->>'research_training_eligible')::boolean,false)
       and labels->'metadata'->>'decision' in('TARGET','NOT_TARGET'));
   labels:=jsonb_set(labels,'{photo_labels}',(select coalesce(jsonb_agg(
     case when exclusions ? (p->>'sha256') then p||jsonb_build_object('pre_review_exclusion',coalesce(p->>'pre_review_exclusion',p->>'exclusion','none'),'exclusion',exclusions->>(p->>'sha256'))
       when p ? 'pre_review_exclusion' then (p-'pre_review_exclusion')||jsonb_build_object('exclusion',p->>'pre_review_exclusion') else p end order by n),'[]'::jsonb)
     from jsonb_array_elements(labels->'photo_labels') with ordinality t(p,n)));
  end if;
  insert into acq_training.dataset_items(workspace_id,dataset_id,group_id,example_id,label_snapshot,photo_hashes,source_review_ids)
  values(candidate_workspace,child_id,item.group_id,item.example_id,labels,item.photo_hashes,
   array(select e.id from acq_training.property_label_events e where e.workspace_id=candidate_workspace
    and e.evidence_snapshot_id=(labels->>'evidence_id')::uuid and e.review_status='approved' and e.provenance='HUMAN_REVIEWED'
    and e.revision<=current_revision order by e.id));
 end loop;
 select jsonb_build_object('policy',policy,
  'groups',(select jsonb_agg(jsonb_build_object('group_id',g.group_id,'split',g.split) order by g.group_id)
    from acq_training.dataset_groups g where g.workspace_id=candidate_workspace and g.dataset_id=child_id),
  'items',(select jsonb_agg(jsonb_build_object('example_id',i.example_id,'group_id',i.group_id,'photo_hashes',i.photo_hashes,
    'label_sha256',encode(extensions.digest(convert_to(i.label_snapshot::text,'UTF8'),'sha256'),'hex')) order by i.example_id)
    from acq_training.dataset_items i where i.workspace_id=candidate_workspace and i.dataset_id=child_id)
 ) into manifest;
 update acq_training.datasets set manifest_sha256=encode(extensions.digest(convert_to(manifest::text,'UTF8'),'sha256'),'hex'),frozen_at=now()
 where workspace_id=candidate_workspace and id=child_id;
 return child_id;
end $$;

create or replace function acq_training.create_paired_condition_candidate(
 candidate_workspace uuid, candidate_release uuid,
 vision_run uuid, text_run uuid, structured_run uuid, fusion_run uuid,
 evaluation jsonb, manifest jsonb, bundle_sha256 text
) returns uuid language plpgsql security definer set search_path='' as $$
declare next_version integer; component record;
begin
 if not acq_training.allowed_workspace(candidate_workspace) then raise exception 'Workspace is not authorized'; end if;
 if manifest->>'schema_version'<>'actvision-paired-condition-v1' or manifest->>'status'<>'candidate'
  or manifest->>'release_id'<>candidate_release::text or manifest->>'kind'<>'release'
  or bundle_sha256 !~ '^[a-f0-9]{64}$' then raise exception 'Invalid paired candidate manifest'; end if;
 if coalesce((evaluation->>'protected_slices_passed')::boolean,false)
  or coalesce((manifest->>'production_ready')::boolean,true) then raise exception 'Paired silver candidates are research-only'; end if;
 if exists(select 1 from unnest(array[vision_run,text_run,structured_run,fusion_run]) r
  where r is not null and not exists(select 1 from acq_training.model_runs m
   where m.workspace_id=candidate_workspace and m.id=r and m.status='completed'
    and m.configuration->>'schema_version'='actvision-paired-condition-v1')) then
  raise exception 'Paired components must reference completed matching runs'; end if;
 if vision_run is null and text_run is null and structured_run is null then raise exception 'A candidate needs an input head'; end if;
 if manifest->>'artifact_key'<>'models/paired-condition/'||candidate_release::text||'/candidate.json' or coalesce(manifest->>'artifact_sha256','') !~ '^[a-f0-9]{64}$'
  or encode(extensions.digest(convert_to(manifest::text,'UTF8'),'sha256'),'hex') is null then raise exception 'Invalid artifact identity'; end if;
 for component in select * from (values ('vision',vision_run),('text',text_run),('structured',structured_run),('fusion',fusion_run)) t(task,run_id) where run_id is not null loop
  if not exists(select 1 from acq_training.model_runs r join acq_training.datasets d on (d.workspace_id,d.id)=(r.workspace_id,r.dataset_id)
    where r.workspace_id=candidate_workspace and r.id=component.run_id and r.target_task=component.task and r.status='completed'
    and r.artifact_object_key=manifest->>'artifact_key' and r.artifact_sha256=manifest->>'artifact_sha256' and d.manifest_sha256=manifest->>'dataset_sha256'
    and manifest->'components'->component.task->>'run_id'=r.id::text) then raise exception 'Component task, dataset or artifact identity differs'; end if;
 end loop;
 perform pg_advisory_xact_lock(hashtextextended(candidate_workspace::text||'paired-condition-release',0));
 select coalesce(max(version),0)+1 into next_version from acq_training.model_releases
  where workspace_id=candidate_workspace and name='paired-condition';
 insert into acq_training.model_releases(workspace_id,id,name,version,vision_run_id,text_run_id,structured_run_id,
  fusion_run_id,status,evaluation_summary,bundle_manifest,bundle_sha256,schema_version,calibration_version)
 values(candidate_workspace,candidate_release,'paired-condition',next_version,vision_run,text_run,structured_run,fusion_run,
  'candidate',evaluation,manifest,bundle_sha256,'actvision-paired-condition-v1','uncalibrated');
 return candidate_release;
end $$;

revoke all on function acq_training.freeze_paired_condition_next(uuid,uuid,text) from public;
revoke all on function acq_training.create_paired_condition_candidate(uuid,uuid,uuid,uuid,uuid,uuid,jsonb,jsonb,text) from public;
grant execute on function acq_training.freeze_paired_condition_next(uuid,uuid,text) to acq_training_reviewer,acq_training_worker;
grant execute on function acq_training.create_paired_condition_candidate(uuid,uuid,uuid,uuid,uuid,uuid,jsonb,jsonb,text) to acq_training_reviewer,acq_training_worker;
commit;

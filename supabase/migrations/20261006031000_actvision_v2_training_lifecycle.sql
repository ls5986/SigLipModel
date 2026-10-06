-- Narrow v2 dataset/run/release lifecycle for the hosted worker.
begin;

alter table acq_training.model_releases
  drop constraint if exists model_releases_status_check,
  drop constraint if exists model_releases_check1;

alter table acq_training.model_releases
  add constraint model_releases_status_check
    check (status in ('candidate','shadow','production','retired')),
  add constraint model_releases_approval_check
    check (
      status not in ('shadow','production')
      or (
        approved_by is not null
        and approved_at is not null
        and coalesce((evaluation_summary->>'protected_slices_passed')::boolean,false)
      )
    );

create or replace function acq_training.freeze_dataset_v2(
  candidate_workspace uuid,
  candidate_dataset uuid,
  candidate_manifest_sha256 text
)
returns void
language plpgsql
security definer
set search_path=''
as $$
begin
  if not acq_training.allowed_workspace(candidate_workspace) then
    raise exception 'Workspace is not authorized';
  end if;
  if candidate_manifest_sha256 !~ '^[a-f0-9]{64}$' then
    raise exception 'Invalid dataset manifest fingerprint';
  end if;
  update acq_training.datasets
     set manifest_sha256=candidate_manifest_sha256, frozen_at=now()
   where workspace_id=candidate_workspace
     and id=candidate_dataset
     and frozen_at is null;
  if not found then
    raise exception 'Dataset is missing or already frozen';
  end if;
end $$;

create or replace function acq_training.queue_model_run_v2(
  candidate_workspace uuid,
  candidate_run uuid,
  candidate_dataset uuid,
  candidate_task text,
  candidate_backbone text,
  candidate_configuration jsonb
)
returns uuid
language plpgsql
security definer
set search_path=''
as $$
begin
  if not acq_training.allowed_workspace(candidate_workspace) then
    raise exception 'Workspace is not authorized';
  end if;
  if candidate_task not in ('vision','text','structured','fusion','actvision_v2') then
    raise exception 'Unsupported v2 task';
  end if;
  if not exists (
    select 1 from acq_training.datasets d
    where d.workspace_id=candidate_workspace
      and d.id=candidate_dataset
      and d.frozen_at is not null
      and d.manifest_sha256 ~ '^[a-f0-9]{64}$'
  ) then
    raise exception 'A frozen dataset is required';
  end if;
  insert into acq_training.model_runs
    (workspace_id,id,dataset_id,target_task,backbone_revision,configuration,status)
  values
    (candidate_workspace,candidate_run,candidate_dataset,candidate_task,
     candidate_backbone,candidate_configuration,'queued');
  return candidate_run;
end $$;

create or replace function acq_training.claim_model_run_v2(
  candidate_workspace uuid,
  candidate_run uuid
)
returns boolean
language plpgsql
security definer
set search_path=''
as $$
declare changed integer;
begin
  if not acq_training.allowed_workspace(candidate_workspace) then
    raise exception 'Workspace is not authorized';
  end if;
  update acq_training.model_runs
     set status='running', error_code=null
   where workspace_id=candidate_workspace
     and id=candidate_run
     and status='queued';
  get diagnostics changed = row_count;
  return changed=1;
end $$;

create or replace function acq_training.finish_model_run_v2(
  candidate_workspace uuid,
  candidate_run uuid,
  candidate_status text,
  candidate_artifact_key text,
  candidate_artifact_sha256 text,
  candidate_metrics jsonb,
  candidate_error_code text
)
returns void
language plpgsql
security definer
set search_path=''
as $$
begin
  if not acq_training.allowed_workspace(candidate_workspace) then
    raise exception 'Workspace is not authorized';
  end if;
  if candidate_status not in ('completed','failed') then
    raise exception 'Invalid final run status';
  end if;
  if candidate_status='completed' and (
    candidate_artifact_key is null or candidate_artifact_key=''
    or candidate_artifact_sha256 !~ '^[a-f0-9]{64}$'
    or candidate_metrics is null
  ) then
    raise exception 'Completed runs require artifact identity and metrics';
  end if;
  update acq_training.model_runs
     set status=candidate_status,
         artifact_object_key=case when candidate_status='completed' then candidate_artifact_key else null end,
         artifact_sha256=case when candidate_status='completed' then candidate_artifact_sha256 else null end,
         metrics=case when candidate_status='completed' then candidate_metrics else null end,
         error_code=case when candidate_status='failed' then coalesce(candidate_error_code,'training_failed') else null end,
         completed_at=now()
   where workspace_id=candidate_workspace
     and id=candidate_run
     and status='running';
  if not found then
    raise exception 'Run is missing or not running';
  end if;
end $$;

create or replace function acq_training.create_candidate_release_v2(
  candidate_workspace uuid,
  candidate_release uuid,
  candidate_name text,
  candidate_version integer,
  candidate_vision_run uuid,
  candidate_text_run uuid,
  candidate_structured_run uuid,
  candidate_fusion_run uuid,
  candidate_evaluation jsonb,
  candidate_manifest jsonb,
  candidate_bundle_sha256 text,
  candidate_calibration_version text
)
returns uuid
language plpgsql
security definer
set search_path=''
as $$
begin
  if not acq_training.allowed_workspace(candidate_workspace) then
    raise exception 'Workspace is not authorized';
  end if;
  if candidate_version < 1 or candidate_bundle_sha256 !~ '^[a-f0-9]{64}$' then
    raise exception 'Invalid candidate identity';
  end if;
  if candidate_manifest->>'kind' <> 'release'
     or candidate_manifest->>'schema_version' <> 'actvision-v2'
     or candidate_manifest->>'status' <> 'candidate'
     or candidate_manifest->>'release_id' <> candidate_release::text then
    raise exception 'Candidate release manifest does not match release identity';
  end if;
  if exists (
    select 1 from unnest(array[candidate_vision_run,candidate_text_run,
                              candidate_structured_run,candidate_fusion_run]) run_id
    where run_id is not null and not exists (
      select 1 from acq_training.model_runs r
      where r.workspace_id=candidate_workspace
        and r.id=run_id and r.status='completed'
    )
  ) then
    raise exception 'All referenced component runs must be completed';
  end if;
  insert into acq_training.model_releases
    (workspace_id,id,name,version,vision_run_id,text_run_id,structured_run_id,
     fusion_run_id,status,evaluation_summary,bundle_manifest,bundle_sha256,
     schema_version,calibration_version,approved_by,approved_at)
  values
    (candidate_workspace,candidate_release,candidate_name,candidate_version,
     candidate_vision_run,candidate_text_run,candidate_structured_run,
     candidate_fusion_run,'candidate',candidate_evaluation,candidate_manifest,
     candidate_bundle_sha256,'actvision-v2',candidate_calibration_version,null,null);
  return candidate_release;
end $$;

revoke all on function acq_training.freeze_dataset_v2(uuid,uuid,text) from public;
revoke all on function acq_training.queue_model_run_v2(uuid,uuid,uuid,text,text,jsonb) from public;
revoke all on function acq_training.claim_model_run_v2(uuid,uuid) from public;
revoke all on function acq_training.finish_model_run_v2(uuid,uuid,text,text,text,jsonb,text) from public;
revoke all on function acq_training.create_candidate_release_v2(uuid,uuid,text,integer,uuid,uuid,uuid,uuid,jsonb,jsonb,text,text) from public;

grant execute on function acq_training.freeze_dataset_v2(uuid,uuid,text)
  to acq_training_reviewer,acq_training_worker;
grant execute on function acq_training.queue_model_run_v2(uuid,uuid,uuid,text,text,jsonb)
  to acq_training_reviewer,acq_training_worker;
grant execute on function acq_training.claim_model_run_v2(uuid,uuid)
  to acq_training_reviewer,acq_training_worker;
grant execute on function acq_training.finish_model_run_v2(uuid,uuid,text,text,text,jsonb,text)
  to acq_training_reviewer,acq_training_worker;
grant execute on function acq_training.create_candidate_release_v2(uuid,uuid,text,integer,uuid,uuid,uuid,uuid,jsonb,jsonb,text,text)
  to acq_training_reviewer,acq_training_worker;

commit;

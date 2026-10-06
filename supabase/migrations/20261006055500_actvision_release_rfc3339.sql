begin;

create or replace function acq_training.promote_release_v2(
  candidate_workspace uuid,
  candidate_release uuid,
  target_status text,
  actor_id text
)
returns void
language plpgsql
security definer
set search_path=''
as $$
declare evaluation jsonb;
declare manifest jsonb;
declare approved_timestamp text;
begin
  if not acq_training.allowed_workspace(candidate_workspace) then
    raise exception 'Workspace is not authorized';
  end if;
  if target_status not in ('shadow','production') then
    raise exception 'Release promotion target must be shadow or production';
  end if;
  if actor_id is null or length(trim(actor_id)) < 1 then
    raise exception 'Operator identity is required';
  end if;

  select evaluation_summary,bundle_manifest
    into evaluation,manifest
    from acq_training.model_releases
   where workspace_id=candidate_workspace
     and id=candidate_release
     and status in ('candidate','shadow')
   for update;
  if not found then
    raise exception 'Candidate release is unavailable for promotion';
  end if;
  if coalesce((evaluation->>'protected_slices_passed')::boolean,false) is not true then
    raise exception 'Protected release gates have not passed';
  end if;
  if target_status='production' and (
    coalesce((evaluation->'acceptance_gates'->>'minimum_human_protected')::boolean,false) is not true
    or coalesce((evaluation->'acceptance_gates'->>'condition_macro_f1')::boolean,false) is not true
    or coalesce((evaluation->'acceptance_gates'->>'modernization_macro_f1')::boolean,false) is not true
    or coalesce((evaluation->'acceptance_gates'->>'acquisition_fit_auroc')::boolean,false) is not true
    or coalesce((evaluation->'acceptance_gates'->>'ece')::boolean,false) is not true
  ) then
    raise exception 'Production quality gates have not passed';
  end if;

  approved_timestamp := to_char(
    clock_timestamp() at time zone 'UTC',
    'YYYY-MM-DD"T"HH24:MI:SS.MS"Z"'
  );
  manifest := jsonb_set(manifest,'{status}',to_jsonb(target_status),false);
  manifest := jsonb_set(manifest,'{approved_by}',to_jsonb(actor_id),false);
  manifest := jsonb_set(manifest,'{approved_at}',to_jsonb(approved_timestamp),false);

  update acq_training.model_releases
     set status=target_status,
         approved_by=actor_id,
         approved_at=approved_timestamp::timestamptz,
         bundle_manifest=manifest
   where workspace_id=candidate_workspace and id=candidate_release;
end $$;

commit;

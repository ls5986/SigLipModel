begin;

create or replace function acq_training.create_frozen_dataset_v2(
  candidate_workspace uuid,
  candidate_dataset uuid,
  candidate_name text,
  candidate_version integer,
  candidate_manifest_sha256 text,
  candidate_label_policy jsonb,
  candidate_groups jsonb,
  candidate_items jsonb
)
returns uuid
language plpgsql
security definer
set search_path=''
as $$
declare group_count integer;
declare item_count integer;
begin
  if not acq_training.allowed_workspace(candidate_workspace) then
    raise exception 'Workspace is not authorized';
  end if;
  if candidate_version < 1 or candidate_manifest_sha256 !~ '^[a-f0-9]{64}$' then
    raise exception 'Invalid dataset identity';
  end if;
  if jsonb_typeof(candidate_groups) <> 'array' or jsonb_typeof(candidate_items) <> 'array' then
    raise exception 'Dataset groups/items must be arrays';
  end if;
  group_count := jsonb_array_length(candidate_groups);
  item_count := jsonb_array_length(candidate_items);
  if group_count < 1 or group_count > 5000 or item_count < 1 or item_count > 10000 then
    raise exception 'Dataset size is outside bounded limits';
  end if;

  insert into acq_training.datasets
    (workspace_id,id,name,version,manifest_sha256,label_policy,frozen_at,purpose)
  values
    (candidate_workspace,candidate_dataset,candidate_name,candidate_version,
     null,candidate_label_policy,null,'training');

  insert into acq_training.dataset_groups(workspace_id,dataset_id,group_id,split)
  select candidate_workspace,candidate_dataset,x.group_id,x.split
  from jsonb_to_recordset(candidate_groups) as x(group_id uuid, split text);

  insert into acq_training.dataset_items
    (workspace_id,dataset_id,group_id,example_id,label_snapshot,photo_hashes,source_review_ids)
  select candidate_workspace,candidate_dataset,x.group_id,x.example_id,x.label_snapshot,
         coalesce(array(select jsonb_array_elements_text(x.photo_hashes)),'{}'::text[]),
         coalesce(array(select (jsonb_array_elements_text(x.source_review_ids))::uuid),'{}'::uuid[])
  from jsonb_to_recordset(candidate_items) as x(
    group_id uuid,
    example_id uuid,
    label_snapshot jsonb,
    photo_hashes jsonb,
    source_review_ids jsonb
  );

  if (select count(*) from acq_training.dataset_groups
      where workspace_id=candidate_workspace and dataset_id=candidate_dataset) <> group_count then
    raise exception 'Dataset group materialization mismatch';
  end if;
  if (select count(*) from acq_training.dataset_items
      where workspace_id=candidate_workspace and dataset_id=candidate_dataset) <> item_count then
    raise exception 'Dataset item materialization mismatch';
  end if;

  update acq_training.datasets
     set manifest_sha256=candidate_manifest_sha256, frozen_at=now()
   where workspace_id=candidate_workspace and id=candidate_dataset;

  return candidate_dataset;
end $$;

revoke all on function acq_training.create_frozen_dataset_v2(uuid,uuid,text,integer,text,jsonb,jsonb,jsonb) from public;
grant execute on function acq_training.create_frozen_dataset_v2(uuid,uuid,text,integer,text,jsonb,jsonb,jsonb)
  to acq_training_reviewer,acq_training_worker;

commit;

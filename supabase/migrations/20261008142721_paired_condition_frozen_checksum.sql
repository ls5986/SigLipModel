begin;
create or replace function acq_training.paired_condition_dataset_sha(w uuid, dataset uuid)
returns text language plpgsql security definer set search_path='' as $$
declare answer text;
begin
 if not acq_training.allowed_workspace(w) then raise exception 'Workspace is not authorized'; end if;
 SELECT encode(extensions.digest(convert_to(jsonb_build_object(
 'policy',d.label_policy,
 'groups',(SELECT jsonb_agg(jsonb_build_object('group_id',g.group_id,'split',g.split) ORDER BY g.group_id) FROM acq_training.dataset_groups g WHERE g.workspace_id=d.workspace_id AND g.dataset_id=d.id),
 'items',(SELECT jsonb_agg(jsonb_build_object('example_id',i.example_id,'group_id',i.group_id,'photo_hashes',i.photo_hashes,'label_sha256',encode(extensions.digest(convert_to(i.label_snapshot::text,'UTF8'),'sha256'),'hex')) ORDER BY i.example_id) FROM acq_training.dataset_items i WHERE i.workspace_id=d.workspace_id AND i.dataset_id=d.id)
 )::text,'UTF8'),'sha256'),'hex') into answer
 FROM acq_training.datasets d WHERE d.workspace_id=w AND d.id=dataset AND d.name='paired-condition-automated-silver' AND d.frozen_at is not null;
 if answer is null then raise exception 'A frozen paired condition dataset is required'; end if;
 return answer;
end $$;
revoke all on function acq_training.paired_condition_dataset_sha(uuid,uuid) from public;
grant execute on function acq_training.paired_condition_dataset_sha(uuid,uuid) to acq_training_reviewer,acq_training_worker;
commit;

-- REVIEW BEFORE APPLYING: forward-only delta for an existing acq_training schema.
-- Run first in a Supabase staging branch using an administrator connection.
begin;

do $$
begin
    if to_regnamespace('acq_training') is null then
        raise exception 'acq_training schema is missing; apply the reviewed base schema instead';
    end if;
end $$;

alter table acq_training.examples
    add column if not exists available_modalities text[] not null default '{}';
alter table acq_training.examples
    drop constraint if exists examples_available_modalities_check;
alter table acq_training.examples
    add constraint examples_available_modalities_check
    check (available_modalities <@ array['images','metadata']::text[]);

alter table acq_training.review_events
    drop constraint if exists review_events_task_check;
alter table acq_training.review_events
    add constraint review_events_task_check check (task in (
        'photo_context','photo_room','photo_features','photo_target_signal',
        'property_condition','property_target','property_priority',
        'property_evidence','comp_quality','economics'
    ));

alter table acq_training.datasets
    add column if not exists purpose text not null default 'training';
alter table acq_training.datasets
    drop constraint if exists datasets_purpose_check;
alter table acq_training.datasets
    add constraint datasets_purpose_check check (
        purpose in ('training','benchmark','challenge','temporal_shadow')
    );

create table if not exists acq_training.evaluation_slices (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    dataset_id uuid not null,
    name text not null,
    definition jsonb not null,
    protected boolean not null default true,
    primary key (workspace_id,id),
    unique (workspace_id,dataset_id,name),
    foreign key (workspace_id,dataset_id)
        references acq_training.datasets(workspace_id,id)
);

create table if not exists acq_training.evaluation_slice_groups (
    workspace_id uuid not null,
    slice_id uuid not null,
    group_id uuid not null,
    primary key (workspace_id,slice_id,group_id),
    foreign key (workspace_id,slice_id)
        references acq_training.evaluation_slices(workspace_id,id),
    foreign key (workspace_id,group_id)
        references acq_training.property_groups(workspace_id,id)
);

create table if not exists acq_training.model_releases (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    name text not null,
    version integer not null check (version>0),
    vision_run_id uuid,
    metadata_run_id uuid,
    fusion_run_id uuid,
    status text not null check (status in ('candidate','approved','retired')),
    evaluation_summary jsonb not null,
    approved_by text,
    approved_at timestamptz,
    created_at timestamptz not null default now(),
    primary key (workspace_id,id),
    unique (workspace_id,name,version),
    foreign key (workspace_id,vision_run_id)
        references acq_training.model_runs(workspace_id,id),
    foreign key (workspace_id,metadata_run_id)
        references acq_training.model_runs(workspace_id,id),
    foreign key (workspace_id,fusion_run_id)
        references acq_training.model_runs(workspace_id,id),
    check (vision_run_id is not null or metadata_run_id is not null),
    check (status <> 'approved' or (approved_by is not null and approved_at is not null))
);

alter table acq_training.predictions
    add column if not exists model_release_id uuid,
    add column if not exists requested_mode text not null default 'automatic',
    add column if not exists mode_used text not null default 'insufficient_evidence',
    add column if not exists component_versions jsonb not null default '{}';
alter table acq_training.predictions
    drop constraint if exists predictions_requested_mode_check;
alter table acq_training.predictions
    add constraint predictions_requested_mode_check check (
        requested_mode in ('automatic','images_only','metadata_only','images_and_metadata')
    );
alter table acq_training.predictions
    drop constraint if exists predictions_mode_used_check;
alter table acq_training.predictions
    add constraint predictions_mode_used_check check (
        mode_used in ('images_only','metadata_only','images_and_metadata','insufficient_evidence')
    );
do $$
begin
    if not exists (
        select 1 from pg_constraint
        where conname='predictions_model_release_fkey'
          and conrelid='acq_training.predictions'::regclass
    ) then
        alter table acq_training.predictions
            add constraint predictions_model_release_fkey
            foreign key (workspace_id,model_release_id)
            references acq_training.model_releases(workspace_id,id);
    end if;
end $$;

alter table acq_training.evaluation_slices enable row level security;
alter table acq_training.evaluation_slice_groups enable row level security;
alter table acq_training.model_releases enable row level security;
revoke all on acq_training.evaluation_slices,
              acq_training.evaluation_slice_groups,
              acq_training.model_releases
    from public,anon,authenticated;

drop policy if exists workspace_access on acq_training.evaluation_slices;
create policy workspace_access on acq_training.evaluation_slices
    to acq_training_reader,acq_training_reviewer,acq_training_worker
    using (acq_training.allowed_workspace(workspace_id))
    with check (acq_training.allowed_workspace(workspace_id));
drop policy if exists workspace_access on acq_training.evaluation_slice_groups;
create policy workspace_access on acq_training.evaluation_slice_groups
    to acq_training_reader,acq_training_reviewer,acq_training_worker
    using (acq_training.allowed_workspace(workspace_id))
    with check (acq_training.allowed_workspace(workspace_id));
drop policy if exists workspace_access on acq_training.model_releases;
create policy workspace_access on acq_training.model_releases
    to acq_training_reader,acq_training_reviewer,acq_training_worker
    using (acq_training.allowed_workspace(workspace_id))
    with check (acq_training.allowed_workspace(workspace_id));

grant select on acq_training.evaluation_slices,
                acq_training.evaluation_slice_groups,
                acq_training.model_releases
    to acq_training_reader,acq_training_reviewer,acq_training_worker;

-- Deliberately no INSERT/UPDATE grants on slices or releases. Membership and
-- promotion remain administrative until narrowly scoped RPCs are reviewed.
commit;

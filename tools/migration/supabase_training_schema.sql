-- DRAFT: apply only through a database administrator after live schema review.
-- No production MLS tables or fields are altered. This schema is NOT exposed
-- through the Supabase Data API. A restricted backend connection is required.
-- Provision a dedicated LOGIN with membership in only the relevant NOLOGIN role;
-- never use postgres, service_role, a table owner or a BYPASSRLS role at runtime.
begin;

create schema acq_training;
revoke all on schema acq_training from public, anon, authenticated;

create role acq_training_reader nologin nosuperuser nobypassrls;
create role acq_training_reviewer nologin nosuperuser nobypassrls;
create role acq_training_worker nologin nosuperuser nobypassrls;

create table acq_training.principals (
    database_login name not null,
    workspace_id uuid not null,
    primary key (database_login, workspace_id)
);
revoke all on acq_training.principals from public, anon, authenticated;

create function acq_training.allowed_workspace(candidate uuid)
returns boolean language sql stable security definer
set search_path = ''
as $$
    select exists (
        select 1 from acq_training.principals p
        where p.database_login = session_user and p.workspace_id = candidate
    )
$$;
revoke all on function acq_training.allowed_workspace(uuid) from public;
grant execute on function acq_training.allowed_workspace(uuid)
    to acq_training_reader, acq_training_reviewer, acq_training_worker;

create table acq_training.property_groups (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    identity_key text not null,
    identity_verified boolean not null default false,
    protected_test boolean not null default false,
    primary key (workspace_id, id),
    unique (workspace_id, identity_key)
);

create table acq_training.examples (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    group_id uuid not null,
    provider text not null,
    listing_key text,
    listing_id text,
    source_workbook_sha256 text not null,
    source_rows integer[] not null,
    target_transaction jsonb not null,
    source_snapshot jsonb not null,
    snapshot_at timestamptz not null,
    available_modalities text[] not null default '{}'
        check (available_modalities <@ array['images','metadata']::text[]),
    match_status text not null check (match_status in ('unresolved','candidate','confirmed','rejected')),
    photo_era text not null default 'unknown'
        check (photo_era in ('unknown','acquisition_candidate','acquisition_confirmed','later_resale')),
    reference_label text check (reference_label in ('GOOD EXAMPLE','NOT TARGET')),
    reference_origin text,
    reference_is_gold boolean not null default false check (not reference_is_gold),
    primary key (workspace_id, id),
    foreign key (workspace_id, group_id) references acq_training.property_groups(workspace_id,id)
);

create table acq_training.photos (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    example_id uuid not null,
    provider_media_key text not null,
    image_sha256 text not null check (length(image_sha256)=64),
    storage_bucket text not null,
    storage_object_key text not null,
    provider_modified_at timestamptz,
    retrieved_at timestamptz not null,
    rights_reference text not null,
    retention_until timestamptz,
    revoked_at timestamptz,
    context text not null default 'unknown' check
        (context in ('unknown','subject','subject_interior','subject_exterior',
                     'shared_amenity','floor_plan','unrelated')),
    context_evidence jsonb not null default '{}',
    primary key (workspace_id,id),
    unique (workspace_id,example_id,provider_media_key,image_sha256),
    foreign key (workspace_id,example_id) references acq_training.examples(workspace_id,id)
);

create table acq_training.review_events (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    example_id uuid not null,
    photo_id uuid,
    task text not null check (task in
        ('photo_context','photo_room','photo_features','photo_target_signal',
         'property_condition','property_target','property_priority',
         'property_evidence','comp_quality','economics')),
    answer jsonb not null,
    status text not null check (status in ('draft','approved','unsure')),
    reviewer_id text not null,
    prediction_was_visible boolean not null,
    prior_event_id uuid,
    created_at timestamptz not null default now(),
    primary key (workspace_id,id),
    foreign key (workspace_id,example_id) references acq_training.examples(workspace_id,id),
    foreign key (workspace_id,photo_id) references acq_training.photos(workspace_id,id),
    foreign key (workspace_id,prior_event_id) references acq_training.review_events(workspace_id,id),
    check ((task like 'photo_%') = (photo_id is not null))
);

create table acq_training.datasets (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    name text not null,
    version integer not null check (version>0),
    purpose text not null default 'training'
        check (purpose in ('training','benchmark','challenge','temporal_shadow')),
    manifest_sha256 text,
    label_policy jsonb not null,
    frozen_at timestamptz,
    primary key (workspace_id,id),
    unique (workspace_id,name,version),
    check (frozen_at is null or length(manifest_sha256)=64)
);

create table acq_training.dataset_groups (
    workspace_id uuid not null,
    dataset_id uuid not null,
    group_id uuid not null,
    split text not null check (split in ('train','validation','test')),
    primary key (workspace_id,dataset_id,group_id),
    foreign key (workspace_id,dataset_id) references acq_training.datasets(workspace_id,id),
    foreign key (workspace_id,group_id) references acq_training.property_groups(workspace_id,id)
);

create table acq_training.dataset_items (
    workspace_id uuid not null,
    dataset_id uuid not null,
    group_id uuid not null,
    example_id uuid not null,
    label_snapshot jsonb not null,
    photo_hashes text[] not null,
    source_review_ids uuid[] not null default '{}',
    primary key (workspace_id,dataset_id,example_id),
    foreign key (workspace_id,dataset_id,group_id)
        references acq_training.dataset_groups(workspace_id,dataset_id,group_id),
    foreign key (workspace_id,example_id) references acq_training.examples(workspace_id,id)
);

create table acq_training.model_runs (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    dataset_id uuid not null,
    target_task text not null,
    backbone_revision text not null,
    configuration jsonb not null,
    status text not null check (status in ('queued','running','failed','completed')),
    artifact_object_key text,
    artifact_sha256 text,
    metrics jsonb,
    error_code text,
    created_at timestamptz not null default now(),
    completed_at timestamptz,
    primary key (workspace_id,id),
    foreign key (workspace_id,dataset_id) references acq_training.datasets(workspace_id,id),
    check (status <> 'completed' or (artifact_object_key is not null and length(artifact_sha256)=64
                                    and metrics is not null and completed_at is not null))
);

create table acq_training.evaluation_slices (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    dataset_id uuid not null,
    name text not null,
    definition jsonb not null,
    protected boolean not null default true,
    primary key (workspace_id,id),
    unique (workspace_id,dataset_id,name),
    foreign key (workspace_id,dataset_id) references acq_training.datasets(workspace_id,id)
);

create table acq_training.evaluation_slice_groups (
    workspace_id uuid not null,
    slice_id uuid not null,
    group_id uuid not null,
    primary key (workspace_id,slice_id,group_id),
    foreign key (workspace_id,slice_id) references acq_training.evaluation_slices(workspace_id,id),
    foreign key (workspace_id,group_id) references acq_training.property_groups(workspace_id,id)
);

create table acq_training.model_releases (
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
    foreign key (workspace_id,vision_run_id) references acq_training.model_runs(workspace_id,id),
    foreign key (workspace_id,metadata_run_id) references acq_training.model_runs(workspace_id,id),
    foreign key (workspace_id,fusion_run_id) references acq_training.model_runs(workspace_id,id),
    check (vision_run_id is not null or metadata_run_id is not null),
    check (status <> 'approved' or (approved_by is not null and approved_at is not null))
);

create table acq_training.predictions (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    run_id uuid not null,
    example_id uuid not null,
    photo_id uuid,
    task text not null,
    evidence_identity text not null,
    model_version text not null,
    model_release_id uuid,
    requested_mode text not null default 'automatic' check
        (requested_mode in ('automatic','images_only','metadata_only','images_and_metadata')),
    mode_used text not null default 'insufficient_evidence' check
        (mode_used in ('images_only','metadata_only','images_and_metadata','insufficient_evidence')),
    component_versions jsonb not null default '{}',
    prompt_hash text,
    bucket text,
    scores jsonb not null,
    coverage jsonb not null,
    decision text check (decision in ('YES','NO','NEEDS_REVIEW')),
    reasons jsonb not null,
    metadata_snapshot jsonb,
    comp_snapshot jsonb,
    cost_assumptions jsonb,
    created_at timestamptz not null default now(),
    primary key (workspace_id,id),
    foreign key (workspace_id,run_id) references acq_training.model_runs(workspace_id,id),
    foreign key (workspace_id,model_release_id) references acq_training.model_releases(workspace_id,id),
    foreign key (workspace_id,example_id) references acq_training.examples(workspace_id,id),
    foreign key (workspace_id,photo_id) references acq_training.photos(workspace_id,id)
);

-- Both source links must refer to the same example, not merely the same workspace.
create function acq_training.validate_links()
returns trigger language plpgsql set search_path = '' as $$
begin
    if new.photo_id is not null and not exists (
        select 1 from acq_training.photos p
        where p.workspace_id=new.workspace_id and p.id=new.photo_id and p.example_id=new.example_id
    ) then raise exception 'Photo belongs to a different example'; end if;
    return new;
end $$;
create trigger review_photo_link before insert on acq_training.review_events
    for each row execute function acq_training.validate_links();
create trigger prediction_photo_link before insert on acq_training.predictions
    for each row execute function acq_training.validate_links();

create function acq_training.guard_dataset()
returns trigger language plpgsql set search_path = '' as $$
declare row_data record;
begin
    if tg_op='DELETE' then row_data := old; else row_data := new; end if;
    if exists(select 1 from acq_training.datasets d where d.workspace_id=row_data.workspace_id
              and d.id=row_data.dataset_id and d.frozen_at is not null)
    then raise exception 'Frozen datasets cannot change'; end if;
    if tg_table_name='dataset_groups' and tg_op<>'DELETE' then
        if new.split<>'test' and exists(select 1 from acq_training.property_groups g
            where g.workspace_id=new.workspace_id and g.id=new.group_id and g.protected_test)
        then raise exception 'Protected test group cannot enter training or validation'; end if;
    end if;
    if tg_table_name='dataset_items' and tg_op<>'DELETE' then
        if not exists(select 1 from acq_training.examples e where e.workspace_id=new.workspace_id
            and e.id=new.example_id and e.group_id=new.group_id)
        then raise exception 'Dataset group differs from example group'; end if;
    end if;
    if tg_op='DELETE' then return old; else return new; end if;
end $$;
create trigger protect_dataset_groups before insert or update or delete on acq_training.dataset_groups
    for each row execute function acq_training.guard_dataset();
create trigger protect_dataset_items before insert or update or delete on acq_training.dataset_items
    for each row execute function acq_training.guard_dataset();

grant usage on schema acq_training to acq_training_reader, acq_training_reviewer, acq_training_worker;
do $$
declare table_name text;
begin
    foreach table_name in array array[
        'property_groups','examples','photos','review_events','datasets','dataset_groups',
        'dataset_items','model_runs','evaluation_slices','evaluation_slice_groups',
        'model_releases','predictions'
    ] loop
        execute format('alter table acq_training.%I enable row level security',table_name);
        execute format('revoke all on acq_training.%I from public,anon,authenticated',table_name);
        execute format(
            'create policy workspace_access on acq_training.%I to acq_training_reader,acq_training_reviewer,acq_training_worker using (acq_training.allowed_workspace(workspace_id)) with check (acq_training.allowed_workspace(workspace_id))',
            table_name);
        execute format('grant select on acq_training.%I to acq_training_reader,acq_training_reviewer,acq_training_worker',table_name);
    end loop;
end $$;
grant insert on acq_training.review_events to acq_training_reviewer;
grant insert on acq_training.property_groups,acq_training.examples,acq_training.photos,
    acq_training.datasets,acq_training.dataset_groups,acq_training.dataset_items,
    acq_training.model_runs,acq_training.predictions to acq_training_worker;
grant update(status,artifact_object_key,artifact_sha256,metrics,error_code,completed_at)
    on acq_training.model_runs to acq_training_worker;
-- Dataset freezing, evaluation-slice membership, identity adjudication and model
-- release promotion stay administrative until narrowly scoped RPCs are reviewed.
-- No worker can edit human answers, protected splits or release status.
-- Source SELECT grants and private Storage object policies are intentionally not
-- included: review actual source views and storage permissions before connecting.
commit;

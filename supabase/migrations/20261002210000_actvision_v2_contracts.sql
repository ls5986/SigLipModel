-- Forward-only, review and apply to staging separately. Never run automatically.
begin;
alter table acq_training.examples drop constraint if exists examples_available_modalities_check;
alter table acq_training.examples add constraint examples_available_modalities_check
    check (available_modalities <@ array['images','metadata','vision','text','structured']::text[]);

alter table acq_training.review_events
    add column if not exists label_schema_version text,
    add column if not exists source_prediction_id text,
    add column if not exists source_evidence jsonb,
    add column if not exists source_feedback_event_id text;
alter table acq_training.review_events drop constraint if exists review_events_task_check;
alter table acq_training.review_events add constraint review_events_task_check check (task in (
    'photo_context','photo_room','photo_features','photo_target_signal',
    'property_condition','property_target','property_priority','property_evidence',
    'comp_quality','economics','property_modernization','property_text_signal'
));
alter table acq_training.model_releases
    add column if not exists text_run_id uuid,
    add column if not exists structured_run_id uuid,
    add column if not exists bundle_manifest jsonb,
    add column if not exists bundle_sha256 text,
    add column if not exists schema_version text,
    add column if not exists calibration_version text;
alter table acq_training.model_releases add constraint model_releases_text_run_fkey
    foreign key (workspace_id,text_run_id) references acq_training.model_runs(workspace_id,id);
alter table acq_training.model_releases add constraint model_releases_structured_run_fkey
    foreign key (workspace_id,structured_run_id) references acq_training.model_runs(workspace_id,id);
do $$
declare constraint_name text;
begin
    for constraint_name in select conname from pg_constraint
        where conrelid='acq_training.model_releases'::regclass and contype='c'
          and pg_get_constraintdef(oid) like '%vision_run_id IS NOT NULL%'
          and pg_get_constraintdef(oid) like '%metadata_run_id IS NOT NULL%'
    loop
        execute format('alter table acq_training.model_releases drop constraint %I', constraint_name);
    end loop;
end $$;
alter table acq_training.model_releases add constraint model_releases_input_component_check check (
    vision_run_id is not null or metadata_run_id is not null or text_run_id is not null or structured_run_id is not null
);
alter table acq_training.predictions
    add column if not exists schema_version text,
    add column if not exists evidence_snapshot jsonb,
    add column if not exists component_results jsonb,
    add column if not exists text_evidence jsonb,
    add column if not exists latency_ms integer check (latency_ms >= 0);

create table acq_training.actvision_feedback_events (
    workspace_id uuid not null,
    event_id text not null,
    source_prediction_id text not null,
    evidence_id text not null check (evidence_id ~ '^[a-f0-9]{64}$'),
    release_id text not null,
    payload_sha256 text not null check (payload_sha256 ~ '^[a-f0-9]{64}$'),
    payload jsonb not null check (
        jsonb_typeof(payload)='object'
        and (payload->>'kind') is not distinct from 'feedback'
        and (payload->>'schema_version') is not distinct from 'actvision-v2'
        and (payload->>'event_id') is not distinct from event_id
        and (payload->>'source_prediction_id') is not distinct from source_prediction_id
        and (payload->>'evidence_id') is not distinct from evidence_id
        and (payload->>'release_id') is not distinct from release_id
    ),
    received_at timestamptz not null default now(),
    primary key (workspace_id,event_id)
);
create table acq_training.actvision_feedback_decisions (
    workspace_id uuid not null,
    id uuid not null default gen_random_uuid(),
    event_id text not null,
    decision text not null check (decision in ('reviewed','rejected')),
    reviewer_id text not null,
    note text not null,
    created_at timestamptz not null default now(),
    primary key (workspace_id,id),
    foreign key (workspace_id,event_id) references acq_training.actvision_feedback_events(workspace_id,event_id)
);
create function acq_training.actvision_append_only()
returns trigger language plpgsql set search_path='' as $$
begin
    raise exception 'ActVision events are append-only';
end $$;
create trigger actvision_feedback_immutable
    before update or delete on acq_training.actvision_feedback_events
    for each row execute function acq_training.actvision_append_only();
create trigger actvision_decision_immutable
    before update or delete on acq_training.actvision_feedback_decisions
    for each row execute function acq_training.actvision_append_only();
alter table acq_training.actvision_feedback_events enable row level security;
alter table acq_training.actvision_feedback_decisions enable row level security;
revoke all on acq_training.actvision_feedback_events,acq_training.actvision_feedback_decisions
    from public,anon,authenticated;
create policy workspace_access on acq_training.actvision_feedback_events
    to acq_training_reader,acq_training_reviewer,acq_training_worker
    using (acq_training.allowed_workspace(workspace_id))
    with check (acq_training.allowed_workspace(workspace_id));
create policy workspace_access on acq_training.actvision_feedback_decisions
    to acq_training_reader,acq_training_reviewer
    using (acq_training.allowed_workspace(workspace_id))
    with check (acq_training.allowed_workspace(workspace_id));
grant select on acq_training.actvision_feedback_events to acq_training_reader,acq_training_reviewer,acq_training_worker;
grant select on acq_training.actvision_feedback_decisions to acq_training_reader,acq_training_reviewer;
grant insert on acq_training.actvision_feedback_events to acq_training_reviewer,acq_training_worker;
grant insert on acq_training.actvision_feedback_decisions to acq_training_reviewer;
-- Existing protected-group and frozen-dataset guards and release grants stay untouched.
commit;

begin;

create index if not exists actvision_feedback_decisions_event_idx
  on acq_training.actvision_feedback_decisions(workspace_id,event_id);

create index if not exists dataset_groups_group_idx
  on acq_training.dataset_groups(workspace_id,group_id);

create index if not exists dataset_items_dataset_group_idx
  on acq_training.dataset_items(workspace_id,dataset_id,group_id);
create index if not exists dataset_items_example_idx
  on acq_training.dataset_items(workspace_id,example_id);

create index if not exists evaluation_slice_groups_group_idx
  on acq_training.evaluation_slice_groups(workspace_id,group_id);

create index if not exists examples_group_idx
  on acq_training.examples(workspace_id,group_id);

create index if not exists model_releases_vision_run_idx
  on acq_training.model_releases(workspace_id,vision_run_id)
  where vision_run_id is not null;
create index if not exists model_releases_metadata_run_idx
  on acq_training.model_releases(workspace_id,metadata_run_id)
  where metadata_run_id is not null;
create index if not exists model_releases_fusion_run_idx
  on acq_training.model_releases(workspace_id,fusion_run_id)
  where fusion_run_id is not null;
create index if not exists model_releases_text_run_idx
  on acq_training.model_releases(workspace_id,text_run_id)
  where text_run_id is not null;
create index if not exists model_releases_structured_run_idx
  on acq_training.model_releases(workspace_id,structured_run_id)
  where structured_run_id is not null;

create index if not exists model_runs_dataset_idx
  on acq_training.model_runs(workspace_id,dataset_id);

create index if not exists predictions_release_idx
  on acq_training.predictions(workspace_id,model_release_id)
  where model_release_id is not null;
create index if not exists predictions_example_idx
  on acq_training.predictions(workspace_id,example_id);
create index if not exists predictions_photo_idx
  on acq_training.predictions(workspace_id,photo_id)
  where photo_id is not null;
create index if not exists predictions_run_idx
  on acq_training.predictions(workspace_id,run_id);

create index if not exists review_events_example_idx
  on acq_training.review_events(workspace_id,example_id);
create index if not exists review_events_photo_idx
  on acq_training.review_events(workspace_id,photo_id)
  where photo_id is not null;
create index if not exists review_events_prior_idx
  on acq_training.review_events(workspace_id,prior_event_id)
  where prior_event_id is not null;

commit;

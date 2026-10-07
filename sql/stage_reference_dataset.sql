BEGIN;
-- Staged membership only. Freeze and training require separate, explicit approval.
INSERT INTO acq_training.datasets(workspace_id,name,version,label_policy,purpose)
SELECT workspace_id,'corrected-prior-sale-reference-review',2,
 '{"mode":"acquisition_reference_similarity_only","provenance":"RULE_DRAFT","classifier_ready":false,"freeze_approved":false,"photo_era_verified":false,"point_in_time_verified":false,"prior_last_labels":"editable_suggestions_not_ground_truth","source":"corrected_paired_workbook_only","supersedes":"earliest_closed_2026_selection","outcomes":"separate_not_features"}'::jsonb,'training'
FROM acq_training.source_imports WHERE schema_version='paired-workbook-v1'
ON CONFLICT DO NOTHING;
CREATE TEMP TABLE reference_candidates ON COMMIT DROP AS
SELECT r.workspace_id,r.group_id,r.source_row_id,r.listing_event_id,s.id evidence_snapshot_id,l.listing_key,l.listing_id,l.raw_snapshot,s.metadata_snapshot,s.photo_manifest,
 row_number() OVER(ORDER BY g.protected_test DESC,EXISTS(SELECT 1 FROM acq_training.dataset_groups dg WHERE dg.workspace_id=r.workspace_id AND dg.group_id=r.group_id AND dg.split='test') DESC,encode(sha256(convert_to(g.identity_key,'UTF8')),'hex')) priority_rank,
 i.source_sha256,d.id dataset_id,src.source_row_number
FROM (SELECT DISTINCT ON(workspace_id,source_row_id,event_role) * FROM acq_training.property_event_roles ORDER BY workspace_id,source_row_id,event_role,revision DESC)r
JOIN acq_training.source_rows src ON src.workspace_id=r.workspace_id AND src.id=r.source_row_id
JOIN acq_training.source_imports i ON i.workspace_id=src.workspace_id AND i.id=src.source_import_id
JOIN acq_training.datasets d ON d.workspace_id=i.workspace_id AND d.name='corrected-prior-sale-reference-review' AND d.version=2
JOIN acq_training.property_groups g ON g.workspace_id=r.workspace_id AND g.id=r.group_id
JOIN acq_training.listing_events l ON l.workspace_id=r.workspace_id AND l.id=r.listing_event_id
JOIN LATERAL(SELECT es.* FROM acq_training.evidence_snapshots es WHERE es.workspace_id=l.workspace_id AND es.listing_event_id=l.id AND es.metadata_snapshot->>'role'='acquisition' ORDER BY es.evidence_sha256 DESC LIMIT 1)s ON true
WHERE r.event_role='acquisition' AND r.decision='machine_candidate';
INSERT INTO acq_training.dataset_groups(workspace_id,dataset_id,group_id,split)
SELECT c.workspace_id,c.dataset_id,c.group_id,
 CASE WHEN c.priority_rank<=50 OR g.protected_test OR EXISTS(SELECT 1 FROM acq_training.dataset_groups old WHERE old.workspace_id=c.workspace_id AND old.group_id=c.group_id AND old.split='test') THEN 'test'
 WHEN EXISTS(SELECT 1 FROM acq_training.dataset_groups old WHERE old.workspace_id=c.workspace_id AND old.group_id=c.group_id AND old.split='validation') OR c.priority_rank<=100 THEN 'validation' ELSE 'train' END
FROM reference_candidates c JOIN acq_training.property_groups g ON g.workspace_id=c.workspace_id AND g.id=c.group_id ON CONFLICT DO NOTHING;
-- Append corrected examples; existing examples and reviews are never re-keyed.
INSERT INTO acq_training.examples(workspace_id,group_id,provider,listing_key,listing_id,source_workbook_sha256,source_rows,target_transaction,source_snapshot,snapshot_at,match_status,photo_era,reference_label,reference_origin,reference_is_gold,available_modalities)
SELECT workspace_id,group_id,'trestle',listing_key,listing_id,source_sha256,ARRAY[source_row_number],'{}'::jsonb,
 jsonb_build_object('corrected_workbook',source_sha256,'event_role','acquisition','evidence_snapshot_id',evidence_snapshot_id,'training_eligible',false,'raw_evidence_reference',listing_event_id),now(),'candidate','acquisition_candidate',NULL,'RULE_DRAFT',false,
 CASE WHEN jsonb_array_length(photo_manifest)>0 THEN ARRAY['images','structured','text'] ELSE ARRAY['structured','text'] END
FROM reference_candidates c WHERE NOT EXISTS(SELECT 1 FROM acq_training.examples e WHERE e.workspace_id=c.workspace_id AND e.group_id=c.group_id AND e.source_workbook_sha256=c.source_sha256 AND e.listing_key=c.listing_key);
INSERT INTO acq_training.dataset_items(workspace_id,dataset_id,group_id,example_id,label_snapshot,photo_hashes)
SELECT c.workspace_id,c.dataset_id,c.group_id,e.id,
 jsonb_build_object('format','paired-acquisition-reference-review-v1','source_sha256',c.source_sha256,'evidence_snapshot_id',c.evidence_snapshot_id,'event_role','acquisition','provenance','RULE_DRAFT','target_suggestion','TARGET','acquisition_fit','UNKNOWN','human_confirmed',false,'metadata',c.metadata_snapshot,'photos',c.photo_manifest),
 ARRAY(SELECT p->>'sha256' FROM jsonb_array_elements(c.photo_manifest)p)
FROM reference_candidates c JOIN LATERAL(SELECT id FROM acq_training.examples e WHERE e.workspace_id=c.workspace_id AND e.group_id=c.group_id AND e.listing_key=c.listing_key AND e.source_workbook_sha256=c.source_sha256 ORDER BY id LIMIT 1)e ON true ON CONFLICT DO NOTHING;
INSERT INTO acq_training.evaluation_slices(workspace_id,dataset_id,name,definition,protected)
SELECT DISTINCT workspace_id,dataset_id,'corrected-prior-sale-human-review-50','{"status":"review_pending","selection":"stable_property_groups_preserving_existing_test_guards","required_labels":["event_role","photo_context","condition","modernization","acquisition_fit"],"quality_claims_allowed":false}'::jsonb,true FROM reference_candidates c
WHERE NOT EXISTS(SELECT 1 FROM acq_training.evaluation_slices s WHERE s.workspace_id=c.workspace_id AND s.dataset_id=c.dataset_id AND s.name='corrected-prior-sale-human-review-50');
INSERT INTO acq_training.evaluation_slice_groups(workspace_id,slice_id,group_id)
SELECT c.workspace_id,s.id,c.group_id FROM reference_candidates c JOIN acq_training.evaluation_slices s ON s.workspace_id=c.workspace_id AND s.dataset_id=c.dataset_id AND s.name='corrected-prior-sale-human-review-50' WHERE c.priority_rank<=50 ON CONFLICT DO NOTHING;
-- Registry is additive; earlier immutable datasets are retained but withdrawn for this workflow.
INSERT INTO acq_training.studio_state(workspace_id,kind,item_id,revision,payload)
SELECT DISTINCT workspace_id,'document','corrected-workbook-data-policy',1,jsonb_build_object('source_sha256',source_sha256,'previous_workbooks_withdrawn',true,'training_approved',false,'freeze_approved',false,'deployment_approved',false,'candidate_dataset_id',dataset_id,'reason','Prior acquisition and later resale must be separate event candidates') FROM reference_candidates ON CONFLICT DO NOTHING;
COMMIT;

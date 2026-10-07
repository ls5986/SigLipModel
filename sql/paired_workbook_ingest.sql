-- Replace :payload with a bound jsonb parameter in the operator runner.
CREATE TEMP TABLE input_rows ON COMMIT DROP AS SELECT value r FROM jsonb_array_elements(:payload::jsonb);
WITH inputs AS (SELECT r FROM input_rows),
imp AS (SELECT * FROM acq_training.source_imports WHERE source_sha256=:source_hash),
added AS (INSERT INTO acq_training.source_rows(workspace_id,source_import_id,source_row_number,group_id,normalized_apn,normalized_unit,address,raw_snapshot,prior_sale_date,prior_recording_date,prior_sale_amount,last_sale_date,last_recording_date,last_sale_amount)
SELECT i.workspace_id,i.id,(r->>'row')::integer,g.id,r->>'normalized_apn',r->>'normalized_unit',coalesce(r->>'address','APN '||(r->>'normalized_apn')),r->'source',
(r->'source'->'simplified'->>'Prior Sale Date')::date,(r->'source'->'simplified'->>'Prior Sale Recordng Date')::date,(r->'source'->'simplified'->>'Prior Sale Amount')::numeric,
(r->'source'->'simplified'->>'Last Sale Date')::date,(r->'source'->'simplified'->>'Last Sale Recording Date')::date,(r->'source'->'simplified'->>'Last Sale Amount')::numeric
FROM inputs CROSS JOIN imp i JOIN acq_training.property_groups g ON g.workspace_id=i.workspace_id AND g.identity_key='sandiego:'||(r->>'normalized_apn')
ON CONFLICT DO NOTHING RETURNING id) SELECT count(*) FROM added;
CREATE TEMP TABLE incoming ON COMMIT DROP AS
SELECT s.workspace_id,s.id source_row_id,s.group_id,ev.value event
FROM input_rows inp
JOIN acq_training.source_imports i ON i.source_sha256=:source_hash
JOIN acq_training.source_rows s ON s.workspace_id=i.workspace_id AND s.source_import_id=i.id AND s.source_row_number=(inp.r->>'row')::integer
CROSS JOIN LATERAL jsonb_array_elements(inp.r->'events') ev;
INSERT INTO acq_training.listing_events(workspace_id,group_id,provider,listing_key,listing_id,standard_status,raw_snapshot,snapshot_at,evidence_sha256)
SELECT workspace_id,group_id,'trestle',event->>'key',v->'raw'->>'ListingId',v->'raw'->>'StandardStatus',v->'raw',(v->>'snapshot_at')::timestamptz,v->>'hash'
FROM incoming CROSS JOIN LATERAL jsonb_array_elements(event->'versions')v ON CONFLICT DO NOTHING;
INSERT INTO acq_training.sale_transactions(workspace_id,group_id,close_date,recording_date,close_price,transaction_identity)
SELECT workspace_id,group_id,(event->'transaction'->>'close_date')::date,(event->'transaction'->>'recording_date')::date,(event->'transaction'->>'close_price')::numeric,event->>'transaction_identity' FROM incoming
ON CONFLICT DO NOTHING;
INSERT INTO acq_training.property_event_roles(workspace_id,source_row_id,group_id,listing_event_id,transaction_id,event_role,decision,provenance,audit,evidence_sha256)
SELECT x.workspace_id,x.source_row_id,x.group_id,l.id,t.id,event->>'role',event->>'decision','RULE_DRAFT',
(event->'audit')||jsonb_build_object('reasons',event->'reasons','suggested_overall',event->'suggested_overall','source_sha256',:source_hash),
encode(sha256(convert_to(event::text,'UTF8')),'hex')
FROM incoming x JOIN acq_training.sale_transactions t ON t.workspace_id=x.workspace_id AND t.group_id=x.group_id AND t.transaction_identity=event->>'transaction_identity'
LEFT JOIN acq_training.listing_events l ON l.workspace_id=x.workspace_id AND l.group_id=x.group_id AND l.listing_key=event->>'key' AND l.evidence_sha256=event->>'selected_hash'
ON CONFLICT DO NOTHING;
INSERT INTO acq_training.transaction_listing_links(workspace_id,transaction_id,listing_event_id,relationship,date_gap_days,match_method,review_status)
SELECT r.workspace_id,r.transaction_id,r.listing_event_id,CASE WHEN r.decision='ambiguous' THEN 'conflicting' ELSE 'primary' END,(x.event->>'gap')::integer,'WORKBOOK_LIFECYCLE_NEAREST_DATE','machine_candidate'
FROM incoming x JOIN acq_training.property_event_roles r ON r.workspace_id=x.workspace_id AND r.source_row_id=x.source_row_id AND r.event_role=x.event->>'role' AND r.revision=0 WHERE r.listing_event_id IS NOT NULL ON CONFLICT DO NOTHING;
-- Exact listing + physical group provenance only. Current bytes must exist and remain usable.
CREATE TEMP TABLE matched_photos ON COMMIT DROP AS
SELECT DISTINCT ON (workspace_id,group_id,listing_key,digest) * FROM (
 SELECT e.workspace_id,e.group_id,e.listing_key,p.image_sha256 digest,p.storage_bucket,p.storage_object_key,p.provider_media_key,p.context
 FROM acq_training.photos p JOIN acq_training.examples e ON e.workspace_id=p.workspace_id AND e.id=p.example_id
 WHERE p.revoked_at IS NULL AND (p.retention_until IS NULL OR p.retention_until>now())
 UNION ALL
 SELECT s.workspace_id,e.group_id,s.payload->>'listing_key',p->>'image_sha256',p->>'storage_bucket',p->>'storage_object_key',p->>'provider_media_key',p->>'context'
 FROM acq_training.studio_state s JOIN acq_training.examples e ON e.workspace_id=s.workspace_id AND e.id::text=s.payload->>'example_id'
 CROSS JOIN LATERAL jsonb_array_elements(s.payload->'images')p WHERE s.kind='document' AND s.item_id LIKE 'mls-validation-media:%'
 UNION ALL
 SELECT r.workspace_id,r.group_id,r.listing_key,p->>'sha256',p->>'storage_bucket',p->>'storage_object_key',split_part(p->>'photo_id',':',2),'unknown'
 FROM acq_training.property_target_review r CROSS JOIN LATERAL jsonb_array_elements(r.event_snapshot->'photos')p
 WHERE split_part(p->>'photo_id',':',1)=r.listing_key
 ) p WHERE EXISTS(SELECT 1 FROM incoming x WHERE x.workspace_id=p.workspace_id AND x.group_id=p.group_id AND x.event->>'key'=p.listing_key)
 AND EXISTS(SELECT 1 FROM storage.objects o WHERE o.bucket_id=p.storage_bucket AND o.name=p.storage_object_key)
 AND NOT EXISTS(SELECT 1 FROM acq_training.photos b WHERE b.workspace_id=p.workspace_id AND b.image_sha256=p.digest AND (b.revoked_at IS NOT NULL OR b.retention_until<=now()))
ORDER BY workspace_id,group_id,listing_key,digest,storage_bucket,storage_object_key,provider_media_key,context;
INSERT INTO acq_training.evidence_snapshots(workspace_id,group_id,listing_event_id,as_of,metadata_snapshot,photo_manifest,available_modalities,feature_policy_version,evidence_sha256)
SELECT x.workspace_id,x.group_id,l.id,l.snapshot_at,event->'metadata',coalesce(p.photos,'[]'::jsonb),
CASE WHEN p.photos IS NULL THEN ARRAY['structured','text'] ELSE ARRAY['structured','text','images'] END,
'paired-review-reference-v1',encode(sha256(convert_to((event->'metadata')::text||coalesce(p.photos,'[]'::jsonb)::text,'UTF8')),'hex')
FROM incoming x JOIN acq_training.listing_events l ON l.workspace_id=x.workspace_id AND l.group_id=x.group_id AND l.listing_key=event->>'key' AND l.evidence_sha256=event->>'selected_hash'
LEFT JOIN LATERAL (SELECT jsonb_agg(jsonb_build_object('listing_key',listing_key,'sha256',digest,'storage_bucket',storage_bucket,'storage_object_key',storage_object_key,'provider_media_key',provider_media_key,'context',context) ORDER BY digest) photos FROM matched_photos p WHERE p.workspace_id=x.workspace_id AND p.group_id=x.group_id AND p.listing_key=l.listing_key)p ON true
ON CONFLICT DO NOTHING;
INSERT INTO acq_training.remark_signal_events(workspace_id,evidence_snapshot_id,source_field,signal_name,state,snippet,start_offset,end_offset,provenance,model_version,review_status)
SELECT DISTINCT x.workspace_id,s.id,z->>'source_field',z->>'signal_name',z->>'state',z->>'snippet',(z->>'start_offset')::integer,(z->>'end_offset')::integer,'RULE_DRAFT','quoted-regex-v1','needs_human_review'
FROM incoming x JOIN acq_training.listing_events l ON l.workspace_id=x.workspace_id AND l.group_id=x.group_id AND l.listing_key=event->>'key' AND l.evidence_sha256=event->>'selected_hash'
JOIN acq_training.evidence_snapshots s ON s.workspace_id=l.workspace_id AND s.listing_event_id=l.id AND s.metadata_snapshot->>'role'=x.event->>'role' CROSS JOIN LATERAL jsonb_array_elements(event->'signals')z
WHERE NOT EXISTS(SELECT 1 FROM acq_training.remark_signal_events r WHERE r.workspace_id=s.workspace_id AND r.evidence_snapshot_id=s.id AND r.source_field=z->>'source_field' AND r.signal_name=z->>'signal_name' AND r.start_offset IS NOT DISTINCT FROM (z->>'start_offset')::integer) ON CONFLICT DO NOTHING;

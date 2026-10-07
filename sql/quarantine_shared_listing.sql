-- Forward-only amendment: one marketing event does not establish a before/after pair.
INSERT INTO acq_training.property_event_roles(workspace_id,source_row_id,group_id,listing_event_id,transaction_id,event_role,decision,provenance,revision,audit,evidence_sha256)
SELECT r.workspace_id,r.source_row_id,r.group_id,r.listing_event_id,r.transaction_id,r.event_role,'ambiguous','RULE_DRAFT',r.revision+1,
 r.audit||jsonb_build_object('reasons',coalesce(r.audit->'reasons','[]'::jsonb)||jsonb_build_array('SHARED_LISTING_REQUIRES_TRANSACTION_REVIEW')),
 encode(sha256(convert_to(r.evidence_sha256||':shared-listing-review','UTF8')),'hex')
FROM acq_training.property_event_roles r JOIN acq_training.source_rows s ON s.workspace_id=r.workspace_id AND s.id=r.source_row_id
WHERE r.revision=0 AND r.listing_event_id IS NOT NULL
 AND s.raw_snapshot->'simplified'->>'Prior Sale MLS'=s.raw_snapshot->'simplified'->>'Last Sale MLS'
 AND NOT EXISTS(SELECT 1 FROM acq_training.property_event_roles q WHERE q.workspace_id=r.workspace_id AND q.source_row_id=r.source_row_id AND q.event_role=r.event_role AND q.revision>0)
ON CONFLICT DO NOTHING;

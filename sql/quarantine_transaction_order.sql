-- Keep original source/roles, append an ambiguity decision for reversed source transactions.
INSERT INTO acq_training.property_event_roles(workspace_id,source_row_id,group_id,listing_event_id,transaction_id,event_role,decision,provenance,revision,audit,evidence_sha256)
SELECT r.workspace_id,r.source_row_id,r.group_id,r.listing_event_id,r.transaction_id,r.event_role,'ambiguous','RULE_DRAFT',r.revision+1,
 r.audit||jsonb_build_object('reasons',coalesce(r.audit->'reasons','[]'::jsonb)||jsonb_build_array('SOURCE_TRANSACTION_ORDER_CONFLICT')),
 encode(sha256(convert_to(r.evidence_sha256||':transaction-order-review','UTF8')),'hex')
FROM (SELECT DISTINCT ON(workspace_id,source_row_id,event_role) * FROM acq_training.property_event_roles ORDER BY workspace_id,source_row_id,event_role,revision DESC)r
JOIN acq_training.source_rows s ON s.workspace_id=r.workspace_id AND s.id=r.source_row_id
WHERE s.last_sale_date<s.prior_sale_date AND NOT(r.audit->'reasons' @> '["SOURCE_TRANSACTION_ORDER_CONFLICT"]'::jsonb)
AND r.provenance='RULE_DRAFT' ON CONFLICT DO NOTHING;

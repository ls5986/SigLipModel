-- Raw paired transactions only. All outcomes remain unreviewed and cannot train a model.
INSERT INTO acq_training.flip_outcomes(workspace_id,group_id,acquisition_transaction_id,resale_transaction_id,acquisition_price,resale_price,gross_uplift_dollars,gross_uplift_percent,hold_days,observation_cutoff,follow_up_days,resale_observed,pair_decision,label_version)
SELECT a.workspace_id,a.group_id,a.transaction_id,b.transaction_id,ta.close_price,tb.close_price,
CASE WHEN a.listing_event_id<>b.listing_event_id AND tb.close_date>=ta.close_date THEN tb.close_price-ta.close_price END,
CASE WHEN a.listing_event_id<>b.listing_event_id AND tb.close_date>=ta.close_date AND ta.close_price>0 THEN (tb.close_price-ta.close_price)/ta.close_price END,
CASE WHEN a.listing_event_id<>b.listing_event_id AND tb.close_date>=ta.close_date THEN tb.close_date-ta.close_date END,
DATE '2026-10-07',GREATEST(0,DATE '2026-10-07'-ta.close_date),
(tb.close_date<=DATE '2026-10-07' AND tb.close_date>=ta.close_date),
CASE WHEN a.listing_event_id=b.listing_event_id THEN 'shared_listing_ambiguous' WHEN tb.close_date<ta.close_date THEN 'chronology_conflict' WHEN tb.close_date>DATE '2026-10-07' THEN 'future_transaction_unverified' ELSE 'needs_human_review' END,'source_pair_draft_v1'
FROM acq_training.property_event_roles a JOIN acq_training.property_event_roles b ON b.workspace_id=a.workspace_id AND b.source_row_id=a.source_row_id AND b.event_role='later_resale' AND b.revision=0
JOIN acq_training.sale_transactions ta ON ta.workspace_id=a.workspace_id AND ta.id=a.transaction_id
JOIN acq_training.sale_transactions tb ON tb.workspace_id=b.workspace_id AND tb.id=b.transaction_id
WHERE a.event_role='acquisition' AND a.revision=0 AND a.listing_event_id IS NOT NULL AND b.listing_event_id IS NOT NULL ON CONFLICT DO NOTHING;

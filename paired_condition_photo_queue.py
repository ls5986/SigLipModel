"""Photo coverage for the review queue. No image downloads or dataset changes."""
def coverage(manifest,tags,excluded=None):
    excluded=excluded or {}
    by_hash={p.get('sha256'):p for p in tags or []}
    usable=[by_hash[p.get('sha256')] for p in manifest or [] if p.get('sha256') in by_hash
            and not excluded.get(p.get('sha256')) and by_hash[p.get('sha256')].get('exclusion')=='none']
    rooms={p.get('room') for p in usable}
    return {'stored':len(manifest or []),'usable':len(usable),'kitchen_bath':'kitchen' in rooms and 'bathroom' in rooms,
            'best_coverage':len(usable)>=5 and 'kitchen' in rooms and 'bathroom' in rooms}

def photo_queue(store):
    from paired_review import PairedReview
    result=PairedReview(store).queue()
    with store.database.connect() as db:
        rows=db.execute('''SELECT DISTINCT ON (r.group_id,r.event_role) r.group_id::text group_id,e.id::text evidence_id,
          e.photo_manifest,(SELECT jsonb_agg(jsonb_build_object('sha256',p->>'sha256',
            'room',p->>'room','exclusion',p->>'exclusion'))
           FROM jsonb_array_elements(a.payload->'photos') p) tags,
          (SELECT answer->'excluded' FROM acq_training.property_label_events x WHERE x.workspace_id=e.workspace_id AND x.evidence_snapshot_id=e.id AND x.label_axis='photo_exclusions' ORDER BY revision DESC LIMIT 1) excluded,
          EXISTS(SELECT 1 FROM acq_training.property_label_events x WHERE x.workspace_id=e.workspace_id AND x.evidence_snapshot_id=e.id AND x.label_axis='overall') reviewed
          FROM acq_training.property_event_roles r
          JOIN acq_training.evidence_snapshots e ON e.workspace_id=r.workspace_id AND e.listing_event_id=r.listing_event_id AND e.group_id=r.group_id AND e.metadata_snapshot->>'role'=r.event_role
          LEFT JOIN acq_training.studio_state a ON a.workspace_id=e.workspace_id AND a.kind='document' AND a.item_id='paired-auto-score:paired-auto-20261008-v1:'||e.id::text
          WHERE r.workspace_id=%s AND r.revision=0
          ORDER BY r.group_id,r.event_role,e.evidence_sha256 DESC''',(store.workspace,)).fetchall()
    groups={}
    for row in rows:
        groups.setdefault(row['group_id'],[]).append({'evidence_id':row['evidence_id'],'reviewed':row['reviewed'],**coverage(row['photo_manifest'],row['tags'],row['excluded'])})
    for item in result['items']:item['photo_events']=groups.get(item['group_id'],[])
    from paired_condition_batches import DOCUMENT,annotate_queue,benchmark_report
    batches=store.document(DOCUMENT) or {}
    result=annotate_queue(result,batches)
    benchmark=result['review_batches'].get('benchmark')
    if benchmark:
        with store.database.connect() as db:
            reviews=db.execute('''SELECT DISTINCT ON (evidence_snapshot_id,label_axis)
              evidence_snapshot_id::text evidence_id,label_axis axis,answer
              FROM acq_training.property_label_events WHERE workspace_id=%s
              AND evidence_snapshot_id=ANY(%s::uuid[]) AND provenance='HUMAN_REVIEWED' AND review_status='approved'
              AND label_axis IN ('image','metadata','overall')
              ORDER BY evidence_snapshot_id,label_axis,revision DESC''',
              (store.workspace,[m['evidence_id'] for m in benchmark['members']])).fetchall()
        benchmark['report']=benchmark_report(benchmark['members'],reviews)
    return result

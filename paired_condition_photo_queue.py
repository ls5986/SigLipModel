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
    import logging
    from time import monotonic
    stage='source_queue'
    started=monotonic()
    try:
        result=PairedReview(store).queue()
    finally:
        logging.warning('studio_queue_timing stage=%s elapsed_ms=%d',stage,int((monotonic()-started)*1000))
    started=monotonic()
    with store.database.connect() as db:
        rows=db.execute('''SELECT DISTINCT ON (r.group_id,r.event_role) r.group_id::text group_id,e.id::text evidence_id,
          e.photo_manifest,
          (SELECT answer->'excluded' FROM acq_training.property_label_events x WHERE x.workspace_id=e.workspace_id AND x.evidence_snapshot_id=e.id AND x.label_axis='photo_exclusions' ORDER BY revision DESC LIMIT 1) excluded,
          EXISTS(SELECT 1 FROM acq_training.property_label_events x WHERE x.workspace_id=e.workspace_id AND x.evidence_snapshot_id=e.id AND x.label_axis='overall') reviewed
          FROM acq_training.property_event_roles r
          JOIN acq_training.evidence_snapshots e ON e.workspace_id=r.workspace_id AND e.listing_event_id=r.listing_event_id AND e.group_id=r.group_id AND e.metadata_snapshot->>'role'=r.event_role
          WHERE r.workspace_id=%s AND r.revision=0
          ORDER BY r.group_id,r.event_role,e.evidence_sha256 DESC''',(store.workspace,)).fetchall()
        logging.warning('studio_queue_timing stage=evidence elapsed_ms=%d',int((monotonic()-started)*1000))
        started=monotonic()
        prefix='paired-auto-score:paired-auto-20261008-v1:'
        keys=[prefix+row['evidence_id'] for row in rows]
        # Fetch only the indexed documents for selected evidence. Joining the entire
        # large JSON document table can produce a disastrous plan under row security.
        docs=db.execute("""SELECT item_id,
          (SELECT jsonb_agg(jsonb_build_object('sha256',p->>'sha256',
            'room',p->>'room','exclusion',p->>'exclusion'))
           FROM jsonb_array_elements(payload->'photos') p) tags
          FROM acq_training.studio_state WHERE workspace_id=%s AND kind='document'
          AND item_id=ANY(%s::text[])""",(store.workspace,keys)).fetchall() if keys else []
        tags={row['item_id']:row['tags'] for row in docs}
        for row in rows:row['tags']=tags.get(prefix+row['evidence_id'])
        logging.warning('studio_queue_timing stage=photo_tags elapsed_ms=%d',int((monotonic()-started)*1000))
    groups={}
    for row in rows:
        groups.setdefault(row['group_id'],[]).append({'evidence_id':row['evidence_id'],'reviewed':row['reviewed'],**coverage(row['photo_manifest'],row['tags'],row['excluded'])})
    for item in result['items']:item['photo_events']=groups.get(item['group_id'],[])
    from paired_condition_batches import DOCUMENT,annotate_queue,benchmark_report
    started=monotonic()
    batches=store.document(DOCUMENT) or {}
    logging.warning('studio_queue_timing stage=batches elapsed_ms=%d',int((monotonic()-started)*1000))
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


def start_read_probe(store):
    """Check the deployed read path once, using its real restricted DB account."""
    import logging
    from threading import Thread
    from time import monotonic
    def run():
        started=monotonic()
        try:
            result=photo_queue(store)
            logging.warning('studio_queue_probe status=ok total=%d elapsed_ms=%d',
                            result['total'],int((monotonic()-started)*1000))
        except Exception as exc:
            logging.warning('studio_queue_probe status=failed error_type=%s sqlstate=%s elapsed_ms=%d',
                            type(exc).__name__,getattr(exc,'sqlstate',None),int((monotonic()-started)*1000))
    Thread(target=run,name='studio-read-probe',daemon=True).start()

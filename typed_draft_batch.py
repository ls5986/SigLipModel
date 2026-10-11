"""Durable, bounded assisted-draft batches. Never approve truth or alter datasets."""
from collections import Counter
from datetime import datetime, timezone
from uuid import uuid4
from studio_data import now
from typed_label_assistant import POLICY, evidence, queue

KEY = 'typed-label-batch-current-v1'
MAX_ITEMS = 500

def request_states(store, ids):
    if not ids: return {}
    names = ['typed-label-request:'+i for i in ids]
    with store.database.connect() as db:
        rows = db.execute("SELECT item_id,payload FROM acq_training.studio_state WHERE workspace_id=%s AND kind='document' AND item_id=ANY(%s)", (store.workspace,names)).fetchall()
    return {r['item_id'].removeprefix('typed-label-request:'):r['payload'] for r in rows}

def status(store):
    batch = store.document(KEY) or {}
    if not batch: return {'status':'none','total':0,'counts':{},'trained_v2':False}
    entries = batch.get('entries', {})
    requests = request_states(store, [i for i,v in entries.items() if v=='queued'])
    counts = Counter(); attention=[]
    for identifier in batch['ids']:
        entry = entries.get(identifier,'waiting')
        if entry == 'queued': entry = requests.get(identifier,{}).get('status','queued')
        counts[entry] += 1
        if entry in {'blocked_source','failed','unavailable'}: attention.append({'id':identifier,'status':entry})
    active = any(counts[k] for k in ('waiting','queued','running'))
    return {'id':batch['id'],'status':'running' if active else 'finished','total':len(batch['ids']),
            'counts':dict(counts),'revision':batch['revision'],'requested_at':batch['requested_at'],
            'trained_v2':False,'needs_attention':attention,'budget_calls_used':(store.document('autolabel-budget:'+datetime.now(timezone.utc).date().isoformat()) or {}).get('calls',0),'daily_limit':(store.document('typed-label-worker') or {}).get('daily_limit'),'notice':'Saved machine drafts only. Source conflicts are blocked; failed paid requests do not retry automatically.'}

def create(store, payload, reviewer):
    if payload.get('confirmed') is not True: raise ValueError('Confirm this paid draft batch')
    if payload.get('selection') == 'all':
        ids=[];offset=0
        while True:
            page=store.queue({'scope':'all','queue':'all','offset':offset,'limit':40})
            ids.extend(
                i['id'] for i in page['items']
                if (i.get('photo_status') or {}).get('state') != 'acquisition_mls_unavailable'
            )
            offset += len(page['items'])
            if offset >= page['total'] or not page['items']: break
            if len(ids)>MAX_ITEMS: raise ValueError('Select 500 or fewer acquisition MLS listings per batch')
    elif payload.get('selection') == 'ids': ids=payload.get('ids')
    else: raise ValueError('Choose all matched listings or selected listing IDs')
    if not isinstance(ids,list) or not ids or len(ids)>MAX_ITEMS or any(not isinstance(i,str) or not i or len(i)>200 for i in ids):
        raise ValueError('Select 1 through 500 existing listings')
    ids=list(dict.fromkeys(ids))
    prior=store.document(KEY) or {}
    if prior and status(store)['status']=='running': return status(store)
    record={'id':uuid4().hex,'status':'dispatching','ids':ids,'cursor':0,'entries':{},'requested_by':reviewer,'requested_at':now(),'policy':POLICY}
    store.save_document(KEY,record,prior.get('revision',0))
    # Archive the selection itself, separately from mutable progress.
    store.save_document('typed-label-batch-selection:'+record['id'],record,0)
    return status(store)

def advance(store):
    """Dispatch at most one paid request per pass; scan a bounded number of skips."""
    batch=store.document(KEY) or {}
    if batch.get('status')!='dispatching': return 0
    entries=dict(batch.get('entries',{}));cursor=batch['cursor'];dispatched=0
    for _ in range(20):
        if cursor>=len(batch['ids']): break
        identifier=batch['ids'][cursor]
        try:
            detail=store.property(identifier)
            if (detail.get('historical_source') or {}).get('blocked'):
                entry='blocked_source'
            else:
                identity=evidence(detail)
                existing=store.document('typed-label-result:'+identifier) or {}
                previous=store.document('typed-label-request:'+identifier) or {}
                if existing.get('label_evidence_id')==identity and existing.get('policy')==POLICY:
                    entry='reused'
                elif previous.get('status') in {'queued','running'}:
                    entry='queued'
                else:
                    queue(store,detail,{'confirmed':True,'label_evidence_id':identity},batch['requested_by'])
                    entry='queued';dispatched=1
        except Exception:
            # No raw source/provider/credential data in batch diagnostics.
            entry='unavailable'
        entries[identifier]=entry;cursor+=1
        if dispatched: break
    store.save_document(KEY,{**batch,'entries':entries,'cursor':cursor,'status':'dispatched' if cursor>=len(batch['ids']) else 'dispatching'},batch['revision'])
    return dispatched

def budget_available(store, classifier):
    if classifier.limit == 0: return True
    key='autolabel-budget:'+datetime.now(timezone.utc).date().isoformat()
    return (store.document(key) or {}).get('calls',0)<classifier.limit

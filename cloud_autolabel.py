"""One local SigLIP worker for cloud review suggestions; no training prerequisite."""
import argparse
import time
from datetime import datetime, timezone

from automatic_labels import POLICY, SiglipLabels
from studio_data import now


def heartbeat(store, status, detail=None):
    key = 'autolabel-worker'
    previous = store.document(key) or {}
    store.save_document(key, {'status':status,'at':now(),'detail':detail}, previous.get('revision',0))


def process(store, identifier, classifier):
    key = 'autolabel-request:'+identifier
    request = store.document(key)
    if not request or request.get('status') not in {'queued','running'}: return False
    policy = getattr(classifier, 'policy', POLICY)
    if request.get('policy') != policy: return False
    if request['status']=='running':
        started = datetime.fromisoformat(request['at'])
        if (datetime.now(timezone.utc)-started).total_seconds()<180: return False
    if request['status']=='running' and policy.startswith('openai-'):
        store.save_document(key,{**request,'status':'failed','at':now(),
            'error':'Worker interrupted. Retry explicitly to resume cached draft tags.'},request['revision'])
        return False
    # Optimistic revision prevents two workers claiming the same request.
    claimed = store.save_document(key,{**request,'status':'running','at':now()},request['revision'])
    images = []
    try:
        detail = store.property(identifier)
        if detail['historical_source']['evidence_hash']!=request['evidence_hash']:
            raise ValueError('Photo set changed; reopen the property to request current labels')
        rows = detail['images']
        for start in range(0,len(rows),4):
            batch = rows[start:start+4]
            paths = [store.image_path(row['id']) for row in batch]
            predictions = classifier.classify(paths)
            if len(predictions)!=len(batch): raise ValueError('Incomplete model results')
            images.extend({**pred,'image_id':row['id'],'sha256':row['sha256']}
                          for row,pred in zip(batch,predictions))
            claimed = store.save_document(key,{**claimed,'at':now()},claimed['revision'])
            heartbeat(store,'running',{'property_id':identifier,'photos':len(images),'total':len(rows)})
        latest = store.property(identifier)
        if latest['historical_source']['evidence_hash']!=request['evidence_hash']:
            raise ValueError('Photo set changed during labeling; results were not published')
        result_key = 'autolabel-result:'+identifier
        previous = store.document(result_key) or {}
        store.save_document(result_key, {'property_id':identifier,'policy':policy,
            'evidence_hash':request['evidence_hash'],'at':now(),'images':images},previous.get('revision',0))
        current = store.document(key)
        if current['revision']==claimed['revision']:
            store.save_document(key,{**current,'status':'completed','at':now()},current['revision'])
        return True
    except Exception:
        # Do not persist provider exceptions, filesystem paths or credentials.
        current = store.document(key)
        if current['revision']==claimed['revision']:
            store.save_document(key,{**current,'status':'failed','at':now(),
                'error':'Labeling failed or photo evidence changed. Check worker configuration, key and quota before retrying.'},current['revision'])
        raise


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--once',action='store_true',help='Process queued requests and exit')
    args=parser.parse_args()
    from cloud_runtime import from_env
    store=from_env().get_studio().store
    heartbeat(store,'loading','Loading frozen SigLIP; first run may download the public checkpoint')
    try:
        classifier=SiglipLabels()
    except Exception:
        heartbeat(store,'failed','Could not load SigLIP. Check local worker installation and model cache.')
        raise
    try:
        last_heartbeat = 0
        while True:
            if time.monotonic()-last_heartbeat>30:
                heartbeat(store,'ready')
                last_heartbeat=time.monotonic()
            for identifier in store.autolabel_pending():
                try: process(store,identifier,classifier)
                except Exception: print('A photo labeling request failed; check the saved request status.',flush=True)
            if args.once: break
            time.sleep(5)
    finally:
        heartbeat(store,'stopped')


if __name__=='__main__': main()

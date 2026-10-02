"""One local SigLIP worker for cloud review suggestions; no training prerequisite."""
import argparse
import time
from datetime import datetime, timezone

from automatic_labels import POLICY, SiglipLabels
from studio_data import now


def heartbeat(store, status, detail=None, stage=None, provider=None):
    key = 'autolabel-room-worker' if stage=='rooms' else 'autolabel-worker'
    if stage=='features' and provider=='copilot': key = 'autolabel-copilot-worker'
    previous = store.document(key) or {}
    store.save_document(key, {'status':status,'at':now(),'detail':detail}, previous.get('revision',0))


def process(store, identifier, classifier):
    key = 'autolabel-request:'+identifier
    request = store.document(key)
    if not request or request.get('status') not in {'queued','running'}: return False
    policy = getattr(classifier, 'policy', POLICY)
    if request.get('policy') != policy: return False
    stage = getattr(classifier,'stage',None)
    if stage and request.get('stage','rooms')!=stage: return False
    if stage=='features' and request.get('label_provider','openai')!=getattr(classifier,'provider','openai'): return False
    if request['status']=='running':
        started = datetime.fromisoformat(request['at'])
        if (datetime.now(timezone.utc)-started).total_seconds()<180: return False
    if request['status']=='running' and (policy.startswith('openai-') or getattr(classifier,'paid',False)):
        store.save_document(key,{**request,'status':'failed','at':now(),
            'error':'Worker interrupted. Retry explicitly to resume cached draft tags.'},request['revision'])
        return False
    # Optimistic revision prevents two workers claiming the same request.
    claimed = store.save_document(key,{**request,'status':'running','at':now()},request['revision'])
    images = []
    usage = {}
    try:
        detail = store.property(identifier)
        if detail['historical_source']['evidence_hash']!=request['evidence_hash']:
            raise ValueError('Photo set changed; reopen the property to request current labels')
        rows = [r for r in detail['images'] if not r.get('synthetic_evidence',{}).get('excluded')]
        prior = store.document('autolabel-result:'+identifier) or {}
        if stage=='features':
            if request.get('mode')!='test' or not prior.get('room_labels_complete'):
                raise ValueError('An explicit small test and SigLIP rooms are required')
            rows = [row for row in rows if row.get('selection',{}).get('included',True)][:8]
            if not rows: raise ValueError('No applicable photos to test')
        for start in range(0,len(rows),4):
            batch = rows[start:start+4]
            paths = [store.image_path(row['id']) for row in batch]
            predictions = classifier.classify(paths,room_tags=[{**row['suggestions'][0],'room':row.get('effective',{}).get('room',row['suggestions'][0]['room'])} for row in batch]) if stage=='features' else classifier.classify(paths)
            for name,value in (getattr(classifier,'last_usage',None) or {}).items():
                if name in {'input_tokens','output_tokens','total_tokens'} and type(value) is int: usage[name] = usage.get(name,0)+value
            if len(predictions)!=len(batch): raise ValueError('Incomplete model results')
            if stage=='features' and detail['historical_source'].get('photo_coverage')=='no_interior':
                predictions = [{**pred,'condition_label':'unknown'} for pred in predictions]
            images.extend({**(row['suggestions'][0] if stage=='features' else {}),**pred,
                          **({'room_source':'SigLIP'} if stage else {}),'image_id':row['id'],'sha256':row['sha256']}
                          for row,pred in zip(batch,predictions))
            claimed = store.save_document(key,{**claimed,'at':now()},claimed['revision'])
            heartbeat(store,'running',{'property_id':identifier,'photos':len(images),'total':len(rows)},stage=stage,provider=getattr(classifier,'provider',None))
        latest = store.property(identifier)
        if latest['historical_source']['evidence_hash']!=request['evidence_hash']:
            raise ValueError('Photo set changed during labeling; results were not published')
        result_key = 'autolabel-result:'+identifier
        previous = store.document(result_key) or {}
        if stage=='features':
            updated = {row['image_id']:row for row in images}
            images = [updated.get(row['image_id'],row) for row in prior['images']]
        store.save_document(result_key, {'property_id':identifier,'policy':policy,
            'evidence_hash':request['evidence_hash'],'at':now(),'images':images,**({'room_labels_complete':True} if stage else {})},previous.get('revision',0))
        current = store.document(key)
        if current['revision']==claimed['revision']:
            next_status = 'queued' if stage=='rooms' and current.get('mode')=='test' else 'awaiting_test' if stage=='rooms' else 'completed'
            store.save_document(key,{**current,'status':next_status,'at':now(),
                **({'stage':'features'} if stage=='rooms' else {}),
                **({'test_photos':len(rows),'usage':usage} if stage=='features' else {})},current['revision'])
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
    import os
    os.environ.setdefault('STUDIO_AUTOLABEL_PROVIDER','hybrid')
    if os.environ['STUDIO_AUTOLABEL_PROVIDER']=='openai': os.environ['STUDIO_AUTOLABEL_PROVIDER']='hybrid'
    store=from_env().get_studio().store
    heartbeat(store,'loading','Loading frozen SigLIP; first run may download the public checkpoint',stage='rooms' if os.environ.get('STUDIO_AUTOLABEL_PROVIDER')=='hybrid' else None)
    try:
        classifier=SiglipLabels()
        from automatic_labels import active_policy
        classifier.policy = active_policy()
        if classifier.policy.startswith('siglip-rooms-openai-'): classifier.stage = 'rooms'
    except Exception:
        heartbeat(store,'failed','Could not load SigLIP. Check local worker installation and model cache.')
        raise
    try:
        last_heartbeat = 0
        while True:
            if time.monotonic()-last_heartbeat>30:
                heartbeat(store,'ready',stage=getattr(classifier,'stage',None))
                last_heartbeat=time.monotonic()
            for identifier in store.autolabel_pending(stage=getattr(classifier,'stage',None)):
                try: process(store,identifier,classifier)
                except Exception: print('A photo labeling request failed; check the saved request status.',flush=True)
            if args.once: break
            time.sleep(5)
    finally:
        heartbeat(store,'stopped',stage=getattr(classifier,'stage',None))


if __name__=='__main__': main()

"""Bounded CPU experiment from explicit unreviewed drafts; no release or truth writes."""
from collections import Counter, defaultdict
from uuid import uuid4
from datetime import datetime, timezone
from actvision_contract import digest
from condition_schema import LABEL_SCHEMA_V2, TEXT_SIGNALS, TARGET_LABELS, validate_labels
from studio_data import now

REQUEST = 'experimental-candidate-request-v1'
MIN_GROUPS = 20
MAX_GROUPS = 500
TASKS = ('physical_condition','modernization','acquisition_fit',*TEXT_SIGNALS)

def supported_classes(rows, task):
    counts = Counter(r['labels'].get(task,'UNKNOWN') for r in rows)
    return {label for label,count in counts.items() if label!='UNKNOWN' and count>=3}

def public_status(store):
    request = store.document(REQUEST) or {'status':'none'}
    bundle = store.document('experimental-candidate:'+request.get('id','')) if request.get('status')=='completed' else None
    return {'request':request, 'candidate':{k:bundle[k] for k in ('id','created_at','policy','counts','evaluation','encoder','dataset_fingerprint','limitations')} if bundle else None,
            'tasks':{task:{'classes':head['classes'],'training_class_counts':head['training_class_counts']} for task,head in (bundle or {}).get('heads',{}).items()},
            'review_next':[{'id':r['id'],'split':r['split'],'labels':{k:v for k,v in r['labels'].items() if k in ('physical_condition','modernization','acquisition_fit')}} for r in sorted((bundle or {}).get('provenance',[]),key=lambda r:(r['split']!='validation',r['id'])) if r['origin']=='unreviewed_ai_draft'][:8],
            'production_ready':False,'approved_release':False}

def enqueue(store, payload, actor):
    if payload.get('confirmed') is not True or payload.get('include_unreviewed_drafts') is not True:
        raise ValueError('Explicitly confirm experimental training with unreviewed AI drafts')
    prior = store.document(REQUEST) or {}
    if prior.get('status') in {'queued','waiting_for_labels','running'}:
        return public_status(store)
    store.save_document(REQUEST, {'id':uuid4().hex,'status':'queued','requested_by':actor,'at':now(),
        'policy':'experimental-unreviewed-drafts-v1','include_unreviewed_drafts':True},prior.get('revision',0))
    return public_status(store)

def select_rows(store, properties):
    """Whole-group protection comes from cloud snapshot, including photo aliases."""
    from studio_v2 import label_evidence, validate_text_reviews
    from typed_label_assistant import POLICY
    groups = defaultdict(list); exclusions=Counter()
    for prop in properties[:MAX_GROUPS]:
        if prop.get('split')=='test': exclusions['protected_group']+=1;continue
        if not prop.get('timing_verified') or prop.get('label_exclusion'): exclusions['source_era']+=1;continue
        detail=store.property(prop['id']); current=detail['property']
        if (detail.get('historical_source') or {}).get('blocked'): exclusions['source_era']+=1;continue
        evidence=label_evidence(prop['id'],current.get('mls_remarks') or '',detail['images'],current.get('metadata'))
        human=current.get('review') or {}
        if human.get('status')=='approved':
            if human.get('label_schema_version')!=LABEL_SCHEMA_V2 or not human.get('reviewer') or human.get('label_evidence_id')!=evidence:
                exclusions['invalid_human_review']+=1;continue
            label=human; origin='approved_human'; proposal_id=None
        else:
            label=store.document('typed-label-result:'+prop['id']) or {}
            if label.get('status')!='draft' or label.get('policy')!=POLICY or label.get('label_evidence_id')!=evidence:
                exclusions['no_current_draft']+=1;continue
            origin='unreviewed_ai_draft';proposal_id=label.get('proposal_id')
            if not proposal_id: exclusions['invalid_draft']+=1;continue
        validate_text_reviews(label.get('text_signals',[]), current.get('mls_remarks') or '')
        physical=label.get('physical_condition','UNKNOWN');modern=label.get('modernization_state','UNKNOWN')
        validate_labels(physical,modern)
        fit={'target':'TARGET','not_target':'NOT_TARGET'}.get(label.get('target_fit'), label.get('acquisition_fit','UNKNOWN'))
        if fit not in TARGET_LABELS: raise ValueError('Invalid acquisition fit')
        from structured_model import completeness
        if not (current.get('mls_remarks') or '').strip() and completeness(current.get('metadata') or {})==0:
            exclusions['no_input_modality']+=1;continue
        labels={'physical_condition':physical,'modernization':modern,'acquisition_fit':fit,
                **{x['signal']:x['state'] for x in label.get('text_signals',[])}}
        group=prop['group_id']
        groups[group].append({'id':prop['id'],'group_id':group,'origin':origin,'labels':labels,
            'remarks':current.get('mls_remarks') or '', 'metadata':current.get('metadata') or {},
            'evidence_id':evidence,'proposal_id':proposal_id,'review_revision':human.get('revision',0),
            'split':'validation' if int(digest({'experimental_group':group})[:8],16)/2**32 < .2 else 'train'})
    rows=[]
    for members in groups.values():
        humans=[r for r in members if r['origin']=='approved_human']; choices=humans or members
        if len({digest(r['labels']) for r in choices})>1: exclusions['conflicting_group']+=len(members);continue
        rows.append(sorted(choices,key=lambda r:r['id'])[0])
    return sorted(rows,key=lambda r:r['group_id']),dict(exclusions)

def encoder():
    from provision_semantic_encoder import verify
    from semantic_text import SemanticTextEncoder
    manifest=verify()
    return SemanticTextEncoder(manifest['directory'],manifest['checkpoint_sha256']), {k:manifest[k] for k in ('model','revision','checkpoint_sha256')}

def metadata_matrix(rows, names=None, scales=None):
    import numpy as np
    from sklearn.feature_extraction import DictVectorizer
    from sklearn.preprocessing import StandardScaler
    from structured_model import structured_features
    features=[structured_features(r['metadata']) for r in rows]
    if names is None:
        vectorizer=DictVectorizer(sparse=False); values=vectorizer.fit_transform(features)
        scaler=StandardScaler(with_mean=False);values=scaler.fit_transform(values)
        return values,vectorizer.get_feature_names_out().tolist(),scaler.scale_.tolist()
    # Only the training vocabulary/scales can enter validation and prediction.
    vocab={name:i for i,name in enumerate(names)}; values=np.zeros((len(rows),len(names)))
    for i,row in enumerate(features):
        for key,value in row.items():
            name=key+'='+value if isinstance(value,str) else key
            if name in vocab: values[i,vocab[name]]=1. if isinstance(value,str) else value
    return values/np.asarray(scales),names,scales

def predict_head(head, matrix):
    import numpy as np
    from scipy.special import expit,softmax
    scores=matrix@np.asarray(head['coef']).T+np.asarray(head['intercept'])
    if len(head['classes'])==2:
        pos=expit(scores[:,0]);return np.column_stack([1-pos,pos])
    return softmax(scores,axis=1)

def train(rows, exclusions, encode, checkpoint):
    import numpy as np
    from sklearn.linear_model import LogisticRegression
    train_rows=[r for r in rows if r['split']=='train']; held=[r for r in rows if r['split']=='validation']
    if len(train_rows)<MIN_GROUPS:
        raise ValueError('Need at least 20 independent non-protected training groups with current labels')
    # Frozen embeddings; supervised heads really fit on these property labels.
    metadata,names,scales=metadata_matrix(train_rows)
    text=encode.transform([r['remarks'] for r in train_rows]).toarray()
    matrix=np.column_stack([text,metadata]);heads={}
    for task in TASKS:
        labels=[r['labels'].get(task,'UNKNOWN') for r in train_rows]
        counts=Counter(v for v in labels if v!='UNKNOWN')
        # A practical experiment floor, not a guarantee of model quality.
        supported=supported_classes(train_rows,task)
        if len(supported)<2:continue
        indices=[i for i,v in enumerate(labels) if v in supported]
        model=LogisticRegression(max_iter=1000,class_weight='balanced',random_state=20261005).fit(matrix[indices],[labels[i] for i in indices])
        heads[task]={'classes':model.classes_.tolist(),'coef':model.coef_.tolist(),'intercept':model.intercept_.tolist(),
                    'training_class_counts':{k:v for k,v in counts.items() if k in supported},
                    'unsupported_class_counts':{k:v for k,v in counts.items() if k not in supported}}
    if not heads: raise ValueError('Need two known classes with at least three independent groups each for a task; UNKNOWN is not negative')
    evaluation={}
    if held:
        meta,_,_=metadata_matrix(held,names,scales)
        validation=np.column_stack([encode.transform([r['remarks'] for r in held]).toarray(),meta])
        for task,head in heads.items():
            predictions=[head['classes'][i] for i in np.argmax(predict_head(head,validation),axis=1)]
            for origin in ('approved_human','unreviewed_ai_draft'):
                pairs=[(pred,r['labels'].get(task,'UNKNOWN')) for pred,r in zip(predictions,held) if r['origin']==origin and r['labels'].get(task,'UNKNOWN')!='UNKNOWN']
                evaluation[task+':'+origin]={'groups':len(pairs),'agreement':sum(a==b for a,b in pairs)/len(pairs) if pairs else None,
                    'meaning':'Held-out agreement with AI drafts; NOT human accuracy' if origin=='unreviewed_ai_draft' else 'Held-out human agreement; small provisional sample'}
    provenance=[{k:r[k] for k in ('id','group_id','origin','labels','evidence_id','proposal_id','review_revision','split')} for r in rows]
    return {'created_at':now(),'policy':'experimental-unreviewed-drafts-v1','encoder':checkpoint,
        'metadata_names':names,'metadata_scales':scales,'text_dimension':text.shape[1],'heads':heads,'evaluation':evaluation,
        'counts':{'train_groups':len(train_rows),'validation_groups':len(held),'origins':dict(Counter(r['origin'] for r in rows)),'excluded':exclusions},
        'dataset_fingerprint':digest(provenance),'provenance':provenance,
        'limitations':['Experimental, uncalibrated; unreviewed AI labels are not human truth',
                       'Description and metadata early combination; no newly trained vision or calibrated late fusion',
                       'Protected groups excluded from fitting and experiment evaluation',
                       'Not approved for /api/actvision/v2/infer, MLS shadow or production']}

def poll_training(store):
    request=store.document(REQUEST) or {}
    if request.get('status')=='running':
        # A durable bundle may have saved just before a restart. Otherwise a
        # bounded CPU job can be retried after its lease, without paid calls.
        saved=store.document('experimental-candidate:'+request['id'])
        if saved:
            store.save_document(REQUEST,{**request,'status':'completed','at':now()},request['revision'])
            return True
        if (datetime.now(timezone.utc)-datetime.fromisoformat(request['at'])).total_seconds()<900:
            return False
        request=store.save_document(REQUEST,{**request,'status':'queued','at':now()},request['revision'])
    if request.get('status') not in {'queued','waiting_for_labels'}:return False
    from cloud_training import snapshot
    _,properties=snapshot(store);rows,excluded=select_rows(store,properties)
    train_rows=[r for r in rows if r['split']=='train']
    counts={task:dict(Counter(r['labels'].get(task,'UNKNOWN') for r in train_rows)) for task in ('physical_condition','modernization','acquisition_fit')}
    enough=len(train_rows)>=MIN_GROUPS and any(len(supported_classes(train_rows,task))>=2 for task in TASKS)
    if not enough:
        from typed_draft_batch import status
        running=status(store)['status']=='running'
        store.save_document(REQUEST,{**request,'status':'waiting_for_labels' if running else 'needs_review',
            'at':now(),'train_groups':len(train_rows),'coverage':counts,'excluded':excluded,
            'reason':'20 independent train groups and two known classes with three groups each are required. UNKNOWN stays unassessed.'},request['revision'])
        return False
    active=store.save_document(REQUEST,{**request,'status':'running','at':now()},request['revision'])
    try:
        enc,checkpoint=encoder();bundle=train(rows,excluded,enc,checkpoint);bundle['id']=request['id']
        # Recheck every selected source/label identity before immutable persistence.
        _,fresh=snapshot(store);latest,_=select_rows(store,fresh)
        by_id={r['id']:r for r in latest}
        if any(digest(by_id.get(r['id'],{}))!=digest(r) for r in rows):raise RuntimeError('Source or labels changed during training')
        store.save_document('experimental-candidate:'+request['id'],bundle,0)
        store.save_document(REQUEST,{**active,'status':'completed','at':now()},active['revision'])
    except Exception:
        store.save_document(REQUEST,{**active,'status':'failed','at':now(),'reason':'Training failed; no candidate promoted. Inspect checkpoint, coverage and worker resources before explicit retry.'},active['revision'])
        raise
    return True

def queue_prediction(store,payload,actor):
    status=public_status(store)
    if not status['candidate']:raise ValueError('No experimental candidate trained yet')
    identifier=str(payload.get('id',''));detail=store.property(identifier)
    from typed_label_assistant import evidence
    if (detail.get('historical_source') or {}).get('blocked'):raise ValueError('Source/era conflict; prediction unavailable')
    key='experimental-prediction:'+identifier;prior=store.document(key) or {}
    if prior.get('status') in {'queued','running'}:return prior
    return store.save_document(key,{'id':identifier,'candidate_id':status['candidate']['id'],'status':'queued',
        'evidence_id':evidence(detail),'requested_by':actor,'at':now()},prior.get('revision',0))

def prediction(store,identifier):
    record=store.document('experimental-prediction:'+identifier)
    if not record:return {'status':'none'}
    from typed_label_assistant import evidence
    detail=store.property(identifier)
    if (detail.get('historical_source') or {}).get('blocked') or evidence(detail)!=record.get('evidence_id'):return {'status':'stale','reason':'Evidence changed; analyze again'}
    return record

def poll_prediction(store):
    with store.database.connect() as db:
        rows=db.execute("SELECT item_id,payload,revision FROM acq_training.studio_state WHERE workspace_id=%s AND kind='document' AND item_id LIKE 'experimental-prediction:%%' AND payload->>'status'='queued' ORDER BY item_id LIMIT 1",(store.workspace,)).fetchall()
    if not rows:return False
    item=rows[0];request={**item['payload'],'revision':item['revision']};active=store.save_document(item['item_id'],{**request,'status':'running'},request['revision'])
    try:
        import numpy as np
        from typed_label_assistant import evidence
        detail=store.property(request['id'])
        if (detail.get('historical_source') or {}).get('blocked') or evidence(detail)!=request['evidence_id']:raise ValueError('Evidence changed')
        bundle=store.document('experimental-candidate:'+request['candidate_id']);enc,checkpoint=encoder()
        if checkpoint!=bundle['encoder']:raise ValueError('Encoder identity changed')
        prop=detail['property']
        from structured_model import completeness
        if not (prop.get('mls_remarks') or '').strip() and completeness(prop.get('metadata') or {})==0:raise ValueError('No usable input modality')
        meta,_,_=metadata_matrix([{'metadata':prop.get('metadata') or {}}],bundle['metadata_names'],bundle['metadata_scales'])
        matrix=np.column_stack([enc.transform([prop.get('mls_remarks') or '']).toarray(),meta])
        results={}
        for task,head in bundle['heads'].items():
            probabilities=predict_head(head,matrix)[0];index=int(np.argmax(probabilities));score=float(probabilities[index])
            results[task]={'label':head['classes'][index] if score>=.6 else 'UNKNOWN','uncalibrated_probability':score,'classes':dict(zip(head['classes'],map(float,probabilities)))}
        if evidence(store.property(request['id']))!=request['evidence_id']:raise ValueError('Evidence changed')
        from cloud_training import snapshot
        _,properties=snapshot(store)
        group=next((r['group_id'] for r in properties if r['id']==request['id']),None)
        trained_on=next((r['split'] for r in bundle['provenance'] if r['group_id']==group),None)
        store.save_document(item['item_id'],{**active,'status':'completed','at':now(),'results':results,
            'training_membership':trained_on,'notice':'Experimental text + metadata prediction; not human truth, calibrated confidence, vision analysis or production inference'},active['revision'])
    except Exception:
        store.save_document(item['item_id'],{**active,'status':'failed','at':now(),'reason':'Experimental prediction failed; inspect evidence and encoder'},active['revision']);raise
    return True

"""Frozen human-review batches; membership never changes with model scores."""
import hashlib

DOCUMENT='paired-condition-review-batches-v1'

def choose_batches(rows,count=25,seed='human-review-20261010-v1'):
    def key(row):return hashlib.sha256((seed+row['group_id']+row['evidence_id']).encode()).hexdigest()
    pool=[r for r in rows if not r.get('reviewed') and r.get('usable',0)>0]
    def pick(candidates):
        selected=[];seen=set()
        for row in candidates:
            if row['group_id'] not in seen:
                selected.append(row);seen.add(row['group_id'])
                if len(selected)==count:break
        return selected
    # The protected sample is selected without decisions or reference labels.
    benchmark=pick(sorted((r for r in pool if r['split']=='test' and r.get('source_split','test')=='test'),key=key))
    def priority(row):
        axes=[(row.get(n) or {}).get('decision') for n in ('image','metadata','text','structured')]
        known={'TARGET','NOT_TARGET'}
        return (int(axes[0] in known and axes[1] in known and axes[0]!=axes[1])*4+
                int(axes[2] in known and axes[3] in known and axes[2]!=axes[3])*2+
                int('INSUFFICIENT_EVIDENCE' in axes)*2,
                int(row.get('kitchen') and row.get('bathroom')),min(row['usable'],10))
    buckets={}
    for row in pool:
        if row['split']=='train':buckets.setdefault(((row.get('metadata') or {}).get('decision'),row.get('role')),[]).append(row)
    for bucket in buckets.values():bucket.sort(key=lambda r:(tuple(-v for v in priority(r)),key(r)))
    ordered=[]
    while any(buckets.values()):
        for name in sorted(buckets,key=str):
            if buckets[name]:ordered.append(buckets[name].pop(0))
    return {'corrections':pick(ordered),'benchmark':benchmark}

def annotate_queue(result,batches):
    result['review_batches']={}
    groups={x['group_id']:x for x in result['items']}
    for name,batch in (batches.get('batches') or {}).items():
        members=batch.get('members') or []
        completed=sum(any(e['evidence_id']==m['evidence_id'] and e.get('reviewed')
                          for e in groups.get(m['group_id'],{}).get('photo_events',[])) for m in members)
        result['review_batches'][name]={'title':batch['title'],'members':members,'total':len(members),
                                      'completed':completed,'baseline_candidate_id':batches['baseline_candidate_id']}
    return result

def benchmark_report(members,reviews):
    latest={(r['evidence_id'],r['axis']):r['answer'] for r in reviews}
    report={}
    for axis in ('image','metadata','overall'):
        pairs=[(m.get('baseline',{}).get(axis,{}).get('decision'),latest.get((m['evidence_id'],axis),{}).get('decision')) for m in members]
        known=[(p,y) for p,y in pairs if y in {'TARGET','NOT_TARGET'}]
        report[axis]={'known':len(known),'correct':sum(p==y for p,y in known),
                      'uncertain':sum(p not in {'TARGET','NOT_TARGET'} for p,y in known),
                      'human_unknown':sum(y=='INSUFFICIENT_EVIDENCE' for p,y in pairs)}
    return report

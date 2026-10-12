from copy import deepcopy
from paired_condition_batches import choose_batches,annotate_queue,benchmark_report

def rows():
    return [{'group_id':str(i),'evidence_id':str(i)+'-'+str(j),'split':'train' if i<30 else 'test',
             'usable':6,'kitchen':True,'bathroom':True,'reviewed':False,'role':'acquisition' if j==0 else 'later_resale',
             'image':{'decision':'TARGET'},'metadata':{'decision':'NOT_TARGET'}} for i in range(60) for j in range(2)]

def test_batches_are_unique_separated_and_deterministic():
    batch=choose_batches(rows())
    assert batch==choose_batches(list(reversed(rows())))
    assert all(len(x)==25 and len({r['group_id'] for r in x})==25 for x in batch.values())
    assert {r['group_id'] for r in batch['corrections']}.isdisjoint(r['group_id'] for r in batch['benchmark'])
    assert all(r['split']=='train' for r in batch['corrections'])
    assert all(r['split']=='test' for r in batch['benchmark'])

def test_benchmark_membership_does_not_use_predictions():
    a=rows();b=deepcopy(a)
    for r in b:r.update(image={'decision':'NOT_TARGET'},metadata={'decision':'TARGET'})
    assert [r['evidence_id'] for r in choose_batches(a)['benchmark']]==[r['evidence_id'] for r in choose_batches(b)['benchmark']]

def test_alias_promoted_validation_group_cannot_be_benchmark_member():
    a=rows()
    for r in a:
        if r['group_id']=='30':r['source_split']='validation'
    assert '30' not in {r['group_id'] for r in choose_batches(a)['benchmark']}

def test_reviewed_and_photoless_not_selected():
    r=rows();r[0]['reviewed']=True;r[1]['usable']=0
    assert '0' not in {x['group_id'] for batch in choose_batches(r).values() for x in batch}

def test_benchmark_unknown_labels_are_not_negative_or_hidden_misses():
    members=[{'evidence_id':'a','baseline':{'metadata':{'decision':'INSUFFICIENT_EVIDENCE'}}},
             {'evidence_id':'b','baseline':{'metadata':{'decision':'NOT_TARGET'}}}]
    report=benchmark_report(members,[{'evidence_id':'a','axis':'metadata','answer':{'decision':'TARGET'}},
                                    {'evidence_id':'b','axis':'metadata','answer':{'decision':'INSUFFICIENT_EVIDENCE'}}])
    assert report['metadata']=={'known':1,'correct':0,'uncertain':1,'human_unknown':1}

def test_batch_progress_counts_selected_listing_not_whole_property():
    result={'items':[{'group_id':'g','reviewed':False,'photo_events':[{'evidence_id':'e','reviewed':True},{'evidence_id':'other','reviewed':False}]}]}
    result=annotate_queue(result,{'baseline_candidate_id':'c','batches':{'corrections':{'title':'Corrections','members':[{'group_id':'g','evidence_id':'e'}]}}})
    assert result['review_batches']['corrections']['completed']==1

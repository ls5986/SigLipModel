from contextlib import contextmanager
from types import SimpleNamespace
import paired_condition as model
import paired_review

class DB:
    def __init__(self, rows): self.rows, self.connections, self.queries = rows, 0, []
    @contextmanager
    def connect(self):
        self.connections += 1
        yield self
    def execute(self, sql, args):
        self.queries.append((sql,args))
        return SimpleNamespace(fetchall=lambda:self.rows)
    def state(self, db, kind, key): return {'stage':'queued'}


def test_detail_batches_documents_and_keeps_photo_facts(monkeypatch):
    candidate={'candidate_id':'c'}
    rows=[{'item_id':'paired-auto-score:paired-auto-20261008-v1:e','revision':2,'payload':{'image':{'decision':'TARGET'},'metadata':{'decision':'NOT_TARGET'},'overall':{'decision':'TARGET'},'photos':[{'sha256':'hash','room':'kitchen','exclusion':'none','decision':'TARGET','strength':70}]}}]
    db=DB(rows)
    monkeypatch.setattr(paired_review.PairedReview,'detail',lambda self, group:{'events':[{'evidence_id':'e','metadata_snapshot':{}}]})
    monkeypatch.setattr(model,'apply_policy',lambda prediction,*args:prediction)
    store=SimpleNamespace(workspace='w',database=db,document=lambda key:candidate)
    result=model.detail(store,'g')
    assert db.connections==1 and len(db.queries)==1
    assert result['events'][0]['photo_tags'][0]['strength']==70
    assert result['events'][0]['silver_reference']['metadata']['decision']=='NOT_TARGET'
    sql,args=db.queries[0]
    assert 'jsonb_build_object' in sql and "'strength',p->'strength'" in sql
    assert args[1]==['paired-condition-inference:e','paired-auto-score:paired-auto-20261008-v1:e','paired-condition-prediction:c:e']


def test_status_batches_documents_and_keeps_progress(monkeypatch):
    db=DB([{'item_id':model.REQUEST,'payload':{'id':'r','status':'queued'},'revision':1}, {'item_id':model.LATEST,'payload':{'candidate_id':'c'},'revision':1}])
    monkeypatch.setattr(model,'latest_dataset',lambda store:dict(id='d',name='frozen',version=4,manifest_sha256='hash',frozen_at='now'))
    result=model.public_status(SimpleNamespace(workspace='w',database=db))
    assert db.connections==1 and len(db.queries)==1
    assert result['request']['status']=='queued' and result['progress']=={'stage':'queued'}
    assert result['candidate']['candidate_id']=='c'


def test_database_timeout_preserves_safe_sqlstate(monkeypatch):
    import pytest
    import psycopg
    from cloud_store import Database
    def fail(*args, **kwargs):
        raise psycopg.errors.QueryCanceled('PRIVATE diagnostics must stay hidden')
    monkeypatch.setattr(psycopg,'connect',fail)
    with pytest.raises(OSError) as caught:
        with Database('not-a-real-credential','eb653547-fcfa-520b-ab4a-0f7359bda381').connect():
            pass
    assert caught.value.sqlstate=='57014'
    assert 'PRIVATE' not in str(caught.value)


def test_photo_queue_fetches_selected_documents_without_large_table_join(monkeypatch):
    import paired_condition_photo_queue as queue
    digest='a'*64
    evidence=[{'group_id':'g','evidence_id':'e','photo_manifest':[{'sha256':digest}], 'excluded':{},'reviewed':False}]
    docs=[{'item_id':'paired-auto-score:paired-auto-20261008-v1:e','tags':[{'sha256':digest,'room':'kitchen','exclusion':'none'}]}]
    db=DB([])
    def execute(sql,args):
        db.queries.append((sql,args))
        return SimpleNamespace(fetchall=lambda:evidence if len(db.queries)==1 else docs)
    db.execute=execute
    monkeypatch.setattr(paired_review.PairedReview,'queue',lambda self:{'items':[{'group_id':'g'}],'total':1})
    store=SimpleNamespace(workspace='w',database=db,document=lambda key:None)
    result=queue.photo_queue(store)
    assert result['items'][0]['photo_events']==[{'evidence_id':'e','reviewed':False,'stored':1,'usable':1,'kitchen_bath':False,'best_coverage':False}]
    assert 'studio_state' not in db.queries[0][0]
    assert db.queries[1][1]==('w',['paired-auto-score:paired-auto-20261008-v1:e'])

import hashlib
import json

import pytest

from cloud_store import SupabaseStore
from test_cloud_runtime import MemoryDatabase, candidate


class Query:
    def __init__(self, rows): self.rows=rows
    def __iter__(self): return iter(self.rows)
    def fetchall(self): return self.rows
    def fetchone(self): return self.rows[0] if self.rows else None


class LedgerDatabase(MemoryDatabase):
    def __init__(self):
        super().__init__()
        self.queries=[]
        self.rows=[]
        for i in range(618):
            selected=candidate()
            selected['listing']['ListingKey']=str(i)
            self.rows.append({'id':str(i),'listing_key':str(i) if i<513 else None,
                'match_status':'candidate' if i<513 else 'unresolved','source_rows':[i+1],'photo_count':1 if i<251 else 0,
                'source_snapshot':{'spreadsheet':{'Address':'Address '+str(i),'OwnerPhone':'hidden'},
                    'mls_candidates':[selected] if i<513 else []}})
        self.photos=[{'listing_key':str(i),'provider_media_key':'photo','image_sha256':'a'*64} for i in range(251)]

    def execute(self, sql, args=None):
        self.queries.append(sql)
        if 'SELECT e.id' in sql: return Query(self.rows)
        if 'SELECT kind,item_id' in sql:
            return Query([{'kind':kind,'item_id':key,'payload':value,'revision':value['revision']}
                          for (kind,key),value in self.states.items()])
        if 'SELECT e.listing_key,p.provider_media_key' in sql: return Query(self.photos)
        if 'SELECT 1 FROM acq_training.examples' in sql: return Query([{'exists':1}] if 1<=args[1]<=618 else [])
        raise AssertionError(sql)


def test_all_618_rows_are_visible_with_bounded_pages_and_sanitized_sources():
    db=LedgerDatabase();store=SupabaseStore(db,None)
    page=store.source_rows({'offset':600,'limit':20})
    assert page['source_rows']==618 and page['total']==618 and len(page['items'])==18
    assert page['counts']=={'verified':0,'verify':251,'rematch':105,'missing_photos':262,'match_confirmed':0}
    assert all('OwnerPhone' not in item['source'] for item in page['items'])
    assert page['items'][-1]['source_row']==618 and page['items'][-1]['listing_key'] is None
    assert len(db.queries)==3


def test_source_approval_requires_current_photo_bytes_and_notes_do_not_approve():
    db=LedgerDatabase();store=SupabaseStore(db,None)
    digest=hashlib.sha256(json.dumps(['a'*64]).encode()).hexdigest()
    db.states['era','0']={'decision':'correct_era','evidence_hash':digest,'revision':1}
    db.states['era','1']={'decision':'correct_era','evidence_hash':'stale','revision':1}
    page=store.source_rows({})
    assert page['items'][0]['status']=='verified' and page['items'][1]['status']=='verify'
    result=store.source_row_note({'source_row':618,'reviewer':'Lindsey','note':'Need prior listing','expected_revision':0})
    assert result['revision']==1
    result=store.source_row_note({'source_row':618,'reviewer':'Lindsey','note':'Correct listing 123','expected_revision':1})
    assert result['revision']==2
    last=store.source_rows({'offset':600})['items'][-1]
    assert last['status']=='rematch' and last['verification_note']['revision']==2
    assert db.rows[-1]['listing_key'] is None
    with pytest.raises(ValueError,match='Unknown workbook'):
        store.source_row_note({'source_row':619,'reviewer':'Lindsey','note':'Unknown','expected_revision':0})


from unittest.mock import Mock

import pytest

from cloud_training import SupabaseJobs, readiness
from model_loop import fingerprint
from pilot import write_json


def property_row(key, target='target', *, verified=True, protected=False, status='approved'):
    return {'id':key, 'group_id':key, 'physical_key':key, 'split':'test' if protected else 'train',
            'review':{'status':status, 'target_fit':target}, 'timing_verified':verified,
            'training_allowed':verified and not protected, 'model_metadata':{'year_built':1960}}


def test_overall_answers_required_and_photo_labels_cannot_supply_them():
    rows = [property_row(str(i), 'target' if i%2 else 'not_target') for i in range(10)]
    rows.append(property_row('pending', status='unreviewed'))
    gate = readiness(rows)
    assert not gate['ready'] and gate['pending_property_reviews'] == 1
    assert gate['eligible_targets'] == 5 and gate['eligible_not_targets'] == 5


def test_uncertain_wrong_era_and_protected_groups_do_not_supervise_property_heads():
    rows = [property_row('yes'+str(i)) for i in range(5)]
    rows += [property_row('no'+str(i), 'not_target') for i in range(5)]
    rows += [property_row('maybe','unsure'), property_row('held', protected=True),
             property_row('wrong', verified=False)]
    gate = readiness(rows)
    assert gate['uncertain'] == 1 and gate['protected_properties'] == 1
    assert gate['eligible_targets'] == 5 and gate['pending_photo_matches'] == 1
    assert not gate['ready']


def test_ready_requires_independent_groups_not_duplicate_properties():
    rows = [property_row(str(i), 'target' if i%2 else 'not_target') for i in range(10)]
    assert readiness(rows)['ready']
    for row in rows: row['group_id'] = row['review']['target_fit']
    assert not readiness(rows)['ready']


def test_cloud_training_blocks_before_photo_download_or_artifact_load(tmp_path, monkeypatch):
    store = Mock()
    jobs = SupabaseJobs(store, tmp_path)
    monkeypatch.setattr('cloud_training.snapshot', lambda _: ([], [property_row('pending',status='draft')]))
    with pytest.raises(ValueError, match='overall target rating'): jobs.preview()
    store.storage.get.assert_not_called()
    assert not list(jobs.folder.glob('*/snapshot.json'))


def test_cloud_preview_cannot_start_after_review_changes(tmp_path, monkeypatch):
    store = Mock()
    jobs = SupabaseJobs(store, tmp_path)
    rows = [property_row(str(i), 'target' if i%2 else 'not_target') for i in range(10)]
    identifier = 'a'*32
    write_json(jobs.folder/identifier/'snapshot.json', {'review_fingerprint':fingerprint([],rows)})
    rows[0]['review']['target_fit'] = 'target'
    monkeypatch.setattr('cloud_training.snapshot', lambda _: ([],rows))
    with pytest.raises(ValueError, match='changed'): jobs.start({'id':identifier})
    assert jobs.active is None


def test_supabase_snapshot_joins_reviews_and_protects_outside_cohort_image_aliases():
    import hashlib
    import json
    from contextlib import contextmanager
    from cloud_store import SupabaseStore
    from cloud_training import COHORT_KEY, snapshot
    keys = [str(i) for i in range(250)]
    records, photos = [], []
    reviews, eras = {}, {}
    for i,key in enumerate(keys):
        digest = hashlib.sha256(key.encode()).hexdigest()
        records.append(dict(id=key,listing_key=key,group_id=key,source_rows=[i],
            identity_key=key,identity_verified=True,protected_test=False,
            source_snapshot={'mls_candidates':[{'listing':{'ListingKey':key,'StandardStatus':'Closed',
                'YearBuilt':1960,'ClosePrice':999999},'match':{'exact_apn':True,
                'street_number_matches':True,'unit_conflict':False,'sale_agreements':[
                    {'source_sale':'prior','price_agrees':True,'minimum_date_gap_days':1}]}}]}))
        photos.append(dict(listing_key=key,group_id=key,provider_media_key='photo',
            image_sha256=digest,storage_bucket='acq-training-private',storage_object_key=key+'/photo'))
        reviews[key] = {'status':'approved','target_fit':'target' if i%2 else 'not_target'}
        eras['era',key] = {'decision':'correct_era','evidence_hash':hashlib.sha256(json.dumps([digest]).encode()).hexdigest()}
    records.append({**records[0],'listing_key':'outside','group_id':'outside','identity_key':'outside'})
    photos.append({**photos[0],'listing_key':'outside','group_id':'outside'})
    class Query:
        def __init__(self,rows): self.rows=rows
        def fetchall(self): return self.rows
    class DB:
        workspace='test'
        @contextmanager
        def connect(self): yield self
        def state(self,db,kind,key):
            assert key==COHORT_KEY
            return {'listing_keys':keys}
        def execute(self,sql,args=None):
            if sql.startswith('SET '): return None
            if 'SELECT e.id' in sql: return Query(records)
            if 'SELECT p.*' in sql: return Query(photos)
            if "i->>'listing_key'" in sql: return Query([{'listing_key':'outside','sha256':photos[0]['image_sha256']}])
            raise AssertionError(sql)
    class Store(SupabaseStore):
        def _legacy(self,db,ids): return {'properties':reviews}
        def _reviews(self,db,ids): return eras
    images, properties = snapshot(Store(DB(),None))
    assert len(properties)==250 and len(images)==250
    assert properties[0]['split']=='test' and not properties[0]['training_allowed']
    assert 50 <= sum(p['split']=='test' for p in properties) <= 51
    assert 'ClosePrice' not in properties[0]['metadata']
    assert properties[0]['model_metadata']['year_built']==1960
    assert all(i['room'] is None and i['preference'] is None and i['features']=={} for i in images)
    assert readiness(properties)['ready']


def test_conflicting_property_answers_in_same_group_block_training():
    rows = [property_row(str(i), 'target' if i%2 else 'not_target') for i in range(10)]
    rows[1]['group_id']=rows[0]['group_id']
    gate=readiness(rows)
    assert not gate['ready'] and 'conflicting' in gate['reasons'][0]


def test_conflicting_protected_group_answers_also_block_evaluation():
    rows=[property_row(str(i), 'target' if i%2 else 'not_target') for i in range(10)]
    a=property_row('held1',protected=True)
    b=property_row('held2','not_target',protected=True)
    b['group_id']=a['group_id']
    assert not readiness(rows+[a,b])['ready']


def test_missing_training_preview_is_a_validation_error(tmp_path):
    jobs=SupabaseJobs(Mock(),tmp_path)
    with pytest.raises(ValueError,match='preview'):
        jobs.start({'id':'b'*32})

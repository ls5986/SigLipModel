import numpy as np
import pytest

from property_models import ACCEPTED_METADATA_KEYS, metadata_features, predict_property
from target_similarity import fit_reference_index, evaluate_reference_index, predict_similarity


def data():
    properties, vectors = [], {}
    for i in range(8):
        properties.append({'id':str(i),'group_id':str(i),'source_rows':[i+1],
            'split':'train' if i<5 else 'test','known_target':True,'training_allowed':i<5,
            'metadata':{'YearBuilt':1970+i,'BedroomsTotal':3,'BathroomsTotalInteger':2,
                        'LivingArea':1500+i*10,'PropertySubType':'Single Family'},
            'review':{'status':'unreviewed'}})
        vectors[str(i)] = [np.asarray([1.,i*.01])]
    return properties,vectors


def test_price_and_outcome_fields_are_not_model_features():
    assert not {'ListPrice','OriginalListPrice','DaysOnMarket','list_price'} & ACCEPTED_METADATA_KEYS
    assert metadata_features({'YearBuilt':1970,'ListPrice':1}) == metadata_features({'YearBuilt':1970,'ListPrice':999999})


def test_positive_only_index_excludes_test_pending_and_rejected_evidence():
    properties,vectors = data()
    properties.append({'id':'pending','group_id':'pending','known_target':False,'split':'train','training_allowed':False})
    index = fit_reference_index(properties,vectors)
    assert [r['property_id'] for r in index['references']] == ['0','1','2','3','4']
    result = predict_property({'target_similarity':index},vectors['6'],properties[6]['metadata'])
    assert result['score_kind']=='known_target_similarity'
    assert result['mode_used']=='images_and_metadata'
    assert result['nearest_examples'] and result['evidence_confidence']=='low'
    assert result['score']==result['component_scores']['combined']
    report=evaluate_reference_index(index,properties,vectors)
    assert report['heldout_groups']==3 and report['components']['combined']['n']==3
    assert 'roc_auc' not in report and 'accuracy' not in report
    assert all(n['group_id']!=p['group_id'] for p in report['properties'] for n in p['nearest_examples'])


def test_explicit_modes_and_sparse_metadata_are_honest():
    properties,vectors=data();index=fit_reference_index(properties,vectors)
    assert predict_similarity(index,[],properties[6]['metadata'])['mode_used']=='metadata_only'
    assert predict_similarity(index,vectors['6'],{})['mode_used']=='images_only'
    missing=predict_similarity(index,[],{'YearBuilt':1970},'metadata_only')
    assert missing['mode_used']=='insufficient_evidence' and missing['score'] is None
    assert predict_similarity(index,[],properties[6]['metadata'],'images_only')['score'] is None


def test_reference_group_aliases_do_not_fill_neighbor_results():
    properties,vectors=data();index=fit_reference_index(properties,vectors)
    index['references'].append({**index['references'][0],'property_id':'duplicate'})
    result=predict_similarity(index,vectors['0'],properties[0]['metadata'])
    assert len({n['group_id'] for n in result['nearest_examples']})==len(result['nearest_examples'])
    result=predict_similarity(index,vectors['0'],properties[0]['metadata'],exclude_group='0')
    assert all(n['group_id']!='0' for n in result['nearest_examples'])


def test_pending_verification_changes_candidate_fingerprint():
    from model_loop import fingerprint
    properties,_=data()
    before=fingerprint([],properties)
    properties[0]['known_target']=False;properties[0]['training_allowed']=False
    assert fingerprint([],properties)!=before


import pytest


@pytest.mark.parametrize("metadata_only", [False, True])
def test_real_training_worker_builds_and_saves_positive_only_candidate(tmp_path, monkeypatch, metadata_only):
    import sys
    import joblib
    import pilot
    import studio_worker
    from sklearn.dummy import DummyClassifier
    properties,vectors=data()
    artifacts=tmp_path/'artifacts';artifacts.mkdir()
    data_root=tmp_path/'data';data_root.mkdir()
    folder=artifacts/'studio_jobs'/'candidate';folder.mkdir(parents=True)
    examples,manifest=[],[]
    for prop in properties:
        path=tmp_path/(prop['id']+'.jpg');path.write_bytes(('bytes-'+prop['id']).encode())
        digest=pilot.sha(path)
        examples.append({'id':prop['id']+':photo','property_id':prop['id'],
            'group_id':prop['group_id'],'physical_key':prop['group_id'],'split':prop['split'],
            'sha256':digest,'path':str(path),'room':None,'features':{},'preference':None,
            'preference_room':'other','photo_context':None,'label_exclusion':None})
        manifest.append({'image_id':prop['id']+':photo','sha256':digest})
    if metadata_only:
        for prop in properties: prop['photo_coverage']='no_interior'
        for row in examples:
            row['label_exclusion']='No interior photos'
            row['path']=None
    # Unverified evidence is never opened or embedded, even if present in the frozen snapshot.
    examples.append({'id':'pending:photo','property_id':'pending','label_exclusion':'Unverified era','path':None})
    pilot.write_json(folder/'snapshot.json',{'kind':'train','objective':'known-target-similarity-v1',
                                           'examples':examples,'properties':properties})
    pilot.write_json(folder/'status.json',{'status':'running'})
    pilot.write_json(artifacts/'backbone.json',{'revision':'b'*40})
    pilot.write_json(data_root/'manifest.json',{'images':manifest})
    np.savez(data_root/'embeddings.npz',image_ids=np.asarray([r['image_id'] for r in manifest]),
             embeddings=np.stack([vectors[p['id']][0] for p in properties]))
    baseline={'backbone_revision':'b'*40,'room_model':DummyClassifier(strategy='constant',constant='kitchen').fit([[0,1]],['kitchen']),
              'feature_models':{},'preference_models':{}}
    monkeypatch.setattr(pilot,'ROOT',tmp_path);monkeypatch.setattr(pilot,'ARTIFACTS',artifacts);monkeypatch.setattr(pilot,'DATA',data_root)
    monkeypatch.setattr(studio_worker,'training_bundle',lambda:(baseline,'silver'))
    monkeypatch.setattr(sys,'argv',['studio_worker.py','--job',str(folder)])
    studio_worker.main()
    saved=joblib.load(folder/'studio_heads.joblib')
    assert saved['property_models']=={}
    assert len(saved['target_similarity']['references'])==5
    metrics=pilot.read_json(folder/'metrics.json')
    assert metrics['objective']=='known-target-similarity-v1'
    assert metrics['protected_evaluation']['heldout_groups']==3
    proposals=pilot.read_json(folder/'proposals.json')
    assert len(proposals)==8 and all(p['property']['target_prediction']['score_kind']=='known_target_similarity' for p in proposals)


def test_no_interior_photos_cannot_teach_visual_similarity_even_with_cached_vectors():
    properties,vectors=data()
    for prop in properties: prop['photo_coverage']='no_interior'
    index=fit_reference_index(properties,vectors)
    assert all(ref['images'] is None for ref in index['references'])
    report=evaluate_reference_index(index,properties,vectors)
    assert report['components']['images']['n']==0 and report['components']['combined']['n']==0
    assert report['components']['metadata']['n']==3
    from model_loop import fingerprint
    before=fingerprint([],properties)
    properties[0]['photo_coverage']='interior_available'
    assert before!=fingerprint([],properties)

import numpy as np
import pytest

from condition_schema import legacy_labels, validate_labels
from v1_models import (
    FusionClassifier, MetadataClassifier, VisionClassifier,
    acquisition_time_metadata,classification_metrics,metadata_row,
    require_class_diversity,
)
from workbench_training import grouped_folds, train_candidate


def synthetic():
    metadata=[];remarks=[];bags=[];labels=[];groups=[]
    for index in range(16):
        target=index>=8
        labels.append(int(target));groups.append('g'+str(index))
        metadata.append({
            'YearBuilt':1960+index if target else 2010+index,
            'LivingArea':1400+index*10,'BedroomsTotal':3,
            'BathroomsTotalInteger':2,'ListPrice':500000+index*1000,
            'OriginalListPrice':520000+index*1000,
            'PropertySubType':'single family','PostalCode':'9210'+str(index%4),
        })
        remarks.append('original finishes cosmetic opportunity' if target else
                       'fully remodeled turnkey home')
        center=1.0 if target else -1.0
        bags.append([np.asarray([center,.2+index/100]),np.asarray([center,.1])])
    return metadata,remarks,bags,np.asarray(labels),groups


def test_v1_models_fit_and_degrade_to_metadata():
    metadata,remarks,bags,y,groups=synthetic()
    require_class_diversity(y,groups)
    meta=MetadataClassifier.fit(metadata,remarks,y)
    vision=VisionClassifier.fit(bags,y,'mean_max')
    meta_scores=meta.predict(metadata,remarks)
    vision_scores=vision.predict(bags)
    fusion=FusionClassifier.fit(meta_scores,vision_scores,[2]*len(y),y)
    combined=fusion.predict(meta_scores,vision_scores,[2]*len(y))
    assert classification_metrics(y,combined)['balanced_accuracy']>=.9
    missing_visual=np.full(len(y),np.nan)
    metadata_only=fusion.predict(meta_scores,missing_visual,[0]*len(y))
    assert np.isfinite(metadata_only).all()
    assert meta.explain(metadata[0],remarks[0])


def test_v1_class_diversity_is_explicit():
    with pytest.raises(ValueError,match='TARGET and NOT_TARGET'):
        require_class_diversity([1]*8,[str(i) for i in range(8)])


def test_v1_sale_history_uses_only_events_before_listing_snapshot():
    enriched=acquisition_time_metadata(
        {'ListDate':'2026-06-01','YearBuilt':1970},
        {'Prior Sale Date':'2020-01-15','Prior Sale Amount':350000,
         'Last Sale Date':'2026-07-01','Last Sale Amount':600000},
    )
    assert enriched['PriorSaleCount']==1
    assert enriched['MostRecentPriorSalePrice']==350000
    assert enriched['MonthsSinceMostRecentPriorSale']>70
    row=metadata_row(enriched)
    assert row['PriorSaleCount']==1
    assert row['MostRecentPriorSalePrice']==350000


def test_condition_and_modernization_mapping_is_separate():
    assert legacy_labels('maintained_original')==('C3_WELL_MAINTAINED','ORIGINAL')
    assert legacy_labels('updated')==('C3_WELL_MAINTAINED','UPDATED')
    assert validate_labels('C5_REHAB_NEEDED','PARTIALLY_UPDATED')==(
        'C5_REHAB_NEEDED','PARTIALLY_UPDATED'
    )


def test_train_candidate_builds_all_components_and_protected_metrics():
    metadata,remarks,bags,y,groups=synthetic()
    rows=[]
    for index in range(16):
        split='train' if index<10 else 'validation' if index<14 else 'test'
        rows.append({
            'property_id':'p'+str(index),'group_id':groups[index],'split':split,
            'target_label':'TARGET' if index%2 else 'NOT_TARGET',
            'hard_negative':bool(index%4==0),
            'physical_condition':'C4_AVERAGE_FUNCTIONAL',
            'modernization_state':'ORIGINAL',
            'metadata':metadata[index],'remarks':remarks[index],
            'vectors':bags[index],'image_count':len(bags[index]),'coverage':'interior_available',
        })
    bundle=train_candidate(rows)
    assert bundle['metadata_model']
    assert bundle['vision_model']
    assert bundle['fusion_model']
    assert bundle['metrics']['protected_test']['fusion']['n']==2
    assert bundle['metrics']['fusion_training']['source']=='grouped_oof_training_predictions'
    assert bundle['metrics']['fusion_training']['validation_labels_used'] is False
    assert bundle['metrics']['fusion_training']['protected_test_used'] is False


def test_oof_folds_are_deterministic_and_group_disjoint():
    rows=[]
    for index in range(12):
        rows.append({'property_id':str(index),'group_id':'g'+str(index),
                     'target_label':'TARGET' if index%2 else 'NOT_TARGET'})
    first,report=grouped_folds(rows,maximum=3)
    second,_=grouped_folds(rows,maximum=3)
    assert report['folds']==3
    assert [(a.tolist(),b.tolist()) for a,b in first]==[
        (a.tolist(),b.tolist()) for a,b in second
    ]
    for fit,holdout in first:
        assert not ({rows[i]['group_id'] for i in fit}&
                    {rows[i]['group_id'] for i in holdout})

import numpy as np
import pytest

from condition_schema import legacy_labels, validate_labels
from v1_models import (
    FusionClassifier, MetadataClassifier, VisionClassifier,
    classification_metrics, require_class_diversity,
)
from workbench_training import train_candidate


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

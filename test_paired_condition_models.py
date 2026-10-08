from copy import deepcopy
import numpy as np

from paired_condition_models import (train, predict, structured_features, sanitize_text,
    guarded_splits, vision_features, classify, combine)


def fixture_rows():
    rows = []
    for group in range(40):
        for role in range(2):
            target = role == 0
            vector = [1. if target else -1., group / 100., .1]
            photos = [{'sha256': f'{group}-{role}', 'room': 'kitchen', 'exclusion': 'none',
                       'decision': 'TARGET' if target else 'NOT_TARGET', 'image_embedding': vector}]
            rows.append({'group_id': str(group), 'evidence_id': f'e-{group}-{role}',
                'listing_key': f'l-{group}-{role}', 'split': 'train' if group < 30 else 'validation' if group < 35 else 'test',
                'eligible': True, 'photos': photos, 'vision_vector': vision_features(photos),
                'text_vector': vector, 'structured': {'YearBuilt': 1960 if target else 2020, 'LivingArea': 1400},
                'labels': {'image': 'TARGET' if target else 'NOT_TARGET',
                           'metadata': 'TARGET' if target else 'NOT_TARGET',
                           'overall': 'TARGET' if target else 'NOT_TARGET'}})
    return rows


def test_actual_heads_fit_and_heldout_labels_do_not_change_them():
    rows = fixture_rows()
    first = train(rows)
    changed = deepcopy(rows)
    for row in changed:
        if row['split'] != 'train':
            row['labels'] = {axis: 'NOT_TARGET' if value == 'TARGET' else 'TARGET' for axis, value in row['labels'].items()}
    second = train(changed)
    assert first['heads'] == second['heads']
    assert first['photo_head'] == second['photo_head']
    assert all(first['heads'][name] for name in ('vision', 'text', 'structured', 'fusion'))
    assert first['evaluation']['test']['image']['agreement'] == 1
    assert first['training_groups'] == 30
    assert first['calibrated'] is False and first['production_ready'] is False


def test_unknown_labels_are_not_negative_and_sparse_modality_is_independent():
    rows = fixture_rows()
    for row in rows:
        row['labels']['image'] = 'INSUFFICIENT_EVIDENCE'
    model = train(rows)
    assert model['heads']['vision'] is None
    assert model['heads']['text'] is not None and model['heads']['structured'] is not None
    assert predict(model, rows[:1])[0]['image']['decision'] == 'INSUFFICIENT_EVIDENCE'


def test_test_aliases_never_enter_fitting():
    rows = fixture_rows()
    rows[0]['listing_key'] = rows[-1]['listing_key']
    guarded = guarded_splits(rows)
    assert guarded[0]['effective_split'] == 'test'
    assert guarded[1]['effective_split'] == 'test'
    model = train(rows)
    assert model['training_groups'] == 29
    assert model['alias_promotions'] == 2


def test_structured_contract_excludes_prices_outcomes_text_ids_and_roles():
    baseline = {'YearBuilt': 1970, 'LivingArea': 1200}
    extras = {'ListPrice': 10, 'OriginalListPrice': 10000, 'ClosePrice': 500000,
              'DaysOnMarket': 100, 'PublicRemarks': 'updated', 'ListingKey': 'x',
              'role': 'later_resale', 'delta_for_selection_only': 900000}
    assert structured_features(baseline) == structured_features({**baseline, **extras})
    assert structured_features({'YearBuilt': '1970'})['YearBuilt'] == 1970
    assert structured_features({'YearBuilt': float('nan')})['YearBuilt_missing'] == 1


def test_photo_prompts_and_silver_decision_never_enter_vision_features():
    photos = fixture_rows()[0]['photos']
    modified = deepcopy(photos)
    modified[0].update(decision='NOT_TARGET', target_logit=1000, margin=-100, strength=100)
    assert vision_features(photos) == vision_features(modified)
    modified[0]['exclusion'] = 'floor_plan'
    assert vision_features(modified) is None


def test_private_access_and_contacts_removed_but_condition_retained():
    text = sanitize_text('Dated kitchen. https://example.com Contact agent@example.com',
                         'Gate code 1234; Call 555-123-4567; Original bathroom needs repair; Mandatory remarks none known')
    assert 'Original bathroom needs repair' in text
    for value in ('1234', '555', 'agent@example.com', 'https://', 'Mandatory'):
        assert value not in text


def test_metadata_target_survives_updated_images_and_missing_images():
    assert combine([classify(.1), classify(.9)])['decision'] == 'TARGET'
    assert combine([classify(None), classify(.9)])['decision'] == 'TARGET'
    assert classify(.5)['decision'] == 'INSUFFICIENT_EVIDENCE'


def test_quarantined_labels_cannot_change_heads():
    rows = fixture_rows()
    extra = deepcopy(rows[0]);extra.update(group_id='q',evidence_id='q',listing_key='q',eligible=False)
    extra['photos'][0]['sha256'] = 'q'
    assert train(rows)['heads'] == train(rows + [extra])['heads']

from paired_condition_evidence import apply_policy,extract

def base():
 return {'status':'completed','image':{'decision':'INSUFFICIENT_EVIDENCE'},'text':{'decision':'NOT_TARGET','affinity':.01},'structured':{'decision':'TARGET','strength':80},'metadata':{'decision':'TARGET','strength':80},'overall':{'decision':'TARGET','strength':99}}
def test_whole_home_renovation_overrides_indirect_structure():
 p=base();r=apply_policy(p,'Fully renovated home with quartz counters.')
 assert r['metadata']['decision']=='NOT_TARGET'
 assert p['metadata']['decision']=='TARGET'
 assert r['learned_prediction']['metadata']['decision']=='TARGET'
 assert r['overall']['decision']=='INSUFFICIENT_EVIDENCE'
 assert apply_policy(r,'Fully renovated home with quartz counters.')==r

def test_partial_updates_negation_and_future_do_not_prove_fully_updated():
 for text in ['Fully renovated kitchen with original bathrooms','Quartz and stainless steel. Move-in ready.','Not fully renovated.','Will be fully renovated.']:
  assert not [x for x in extract(text,'') if x['category']=='fully_updated']
 assert apply_policy(base(),'Fully renovated kitchen with original bathroom')['metadata']['decision']=='TARGET'

def test_explicit_dated_and_text_unknown_combination():
 assert apply_policy(base(),"Original appliances and pastel bathroom")['metadata']['strength']==50
 p=base();p['structured']={'decision':'INSUFFICIENT_EVIDENCE'}
 assert apply_policy(p,'Beautiful home')['metadata']['decision']=='NOT_TARGET'

def test_photo_target_remains_independent():
 p=base();p['image']={'decision':'TARGET','strength':69}
 r=apply_policy(p,'Completely remodeled home')
 assert r['metadata']['decision']=='NOT_TARGET' and r['overall']['decision']=='INSUFFICIENT_EVIDENCE'

def test_comprehensive_kitchen_bath_renovation_and_turnkey_throughout():
 text='Move-in ready and beautifully renovated. A fully remodeled kitchen and bathroom — true turnkey quality throughout. Quartz and stainless steel.'
 assert apply_policy(base(),text)['metadata']['decision']=='NOT_TARGET'


def test_review_comment_phrases_are_extracted_with_source_offsets():
 for text in ['Kitchen needs total overhaul.', 'Ready for remodeling.',
              'Original kitchen and bathrooms.']:
  signals=extract(text,'')
  assert signals and signals[0]['category']=='dated'
  assert text[signals[0]['start_offset']:signals[0]['end_offset']]==signals[0]['phrase']
  assert apply_policy(base(),text)['metadata']['decision']=='TARGET'


def test_marketing_price_and_negated_work_do_not_establish_condition():
 for text in ["Charming 1950's house",'Rare opportunity to own space and serenity',
              'Huge price reduction!','Calling all investors and flippers.',
              'No kitchen needs total overhaul.', 'Not ready for remodeling.']:
  assert not extract(text,'')


def test_vague_potential_does_not_override_updated_photos():
 p=base();p['image']={'decision':'NOT_TARGET'}
 r=apply_policy(p,'Offering great potential to update and personalize.')
 assert r['metadata']['decision']=='INSUFFICIENT_EVIDENCE'
 assert r['overall']['decision']=='NOT_TARGET'
 assert r['evidence_policy']['signals'][0]['confidence']=='low'


def test_room_subject_before_update_does_not_mean_whole_home():
 for text in ['Kitchen is fully renovated.', 'Bathrooms were recently updated.', 'Newly remodeled kitchen opens to living room.']:
  assert not [x for x in extract(text,'') if x['category']=='fully_updated']
  assert [x for x in extract(text,'') if x['category']=='room_updated']
 r=apply_policy(base(),'Kitchen is fully renovated with original bathrooms.')
 assert r['metadata']['decision']=='TARGET'
 assert r['evidence_policy']['condition_signal']=='mixed_condition'


def test_strong_disagreement_is_review_and_missing_photos_allow_explicit_target():
 p=base();p['image']={'decision':'NOT_TARGET'}
 r=apply_policy(p,'Kitchen needs total overhaul.')
 assert r['overall']['decision']=='INSUFFICIENT_EVIDENCE' and r['evidence_policy']['needs_review']
 p['image']={'decision':'INSUFFICIENT_EVIDENCE'}
 assert apply_policy(p,'Kitchen needs total overhaul.')['overall']['decision']=='TARGET'


def test_negation_future_and_whole_home_conflict():
 for text in ['No longer dated.', 'Not considered a fixer.', 'Could easily become fully renovated.', 'Plans call for a fully renovated kitchen.']:
  assert not extract(text,'')
 r=apply_policy(base(),'Fully renovated home. Kitchen needs total overhaul.')
 assert r['metadata']['decision']=='INSUFFICIENT_EVIDENCE'
 assert r['evidence_policy']['conflict']


def test_single_uncertain_modality_does_not_force_weak_target():
 p=base();p['image']={'decision':'TARGET','strength':50};p['metadata']={'decision':'INSUFFICIENT_EVIDENCE'}
 assert apply_policy(p,'')['overall']['decision']=='INSUFFICIENT_EVIDENCE'
 p['image']['strength']=80;p['usable_photo_count']=5
 assert apply_policy(p,'')['overall']['decision']=='TARGET'

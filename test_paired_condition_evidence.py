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
 assert r['metadata']['decision']=='NOT_TARGET' and r['overall']['strength']==69

def test_comprehensive_kitchen_bath_renovation_and_turnkey_throughout():
 text='Move-in ready and beautifully renovated. A fully remodeled kitchen and bathroom — true turnkey quality throughout. Quartz and stainless steel.'
 assert apply_policy(base(),text)['metadata']['decision']=='NOT_TARGET'

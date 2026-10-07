import copy,sys,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
sys.path.insert(0,str(Path(__file__).resolve().parent.parent/'recovery-build'))
from paired_review import validate
from paired_workbook_import import signals,prepare,fingerprint,STRUCTURED
G='11111111-1111-4111-8111-111111111111'
def answer():return {'group_id':G,'evidence_id':G,'role_id':G,'expected_revision':0,'overall':'TARGET','image':{'decision':'NOT_TARGET','strength':None},'metadata':{'decision':'TARGET','strength':75},'notes':'','role_decision':'human_confirmed'}
class Review(unittest.TestCase):
 def test_modalities_are_independent(self):
  a,r=validate(answer());self.assertEqual(a['image']['decision'],'NOT_TARGET');self.assertEqual(a['metadata']['strength'],75)
 def test_missing_role_cannot_save_as_verified(self):
  a=answer();a.pop('role_decision')
  with self.assertRaises(ValueError):validate(a)
 def test_scores_are_not_probabilities(self):
  for n in [49,101,True,'75']:
   a=answer();a['metadata']['strength']=n
   with self.assertRaises(ValueError):validate(a)
 def test_unknown_is_not_negative(self):
  a=answer();a['image']={'decision':'INSUFFICIENT_EVIDENCE','strength':None};self.assertEqual(validate(a)[0]['image']['decision'],'INSUFFICIENT_EVIDENCE')
 def test_photo_exclusions_are_typed(self):
  a=answer();a['excluded_photos']={'a'*64:'virtual_staging'};self.assertEqual(validate(a)[0]['excluded_photos'],a['excluded_photos'])
  a['excluded_photos']={'a'*64:'score_negative'}
  with self.assertRaises(ValueError):validate(a)
 def test_remark_offsets_and_negation(self):
  text='No deferred maintenance. This remodeled kitchen has quartz.'
  ss=signals({'PublicRemarks':text});matching=[s for s in ss if s['snippet']]
  for s in matching:self.assertEqual(text[s['start_offset']:s['end_offset']],s['snippet'])
  neg=next(s for s in matching if s['signal_name']=='deferred_maintenance');self.assertEqual(neg['state'],'ABSENT');self.assertEqual(neg['snippet'],'No deferred maintenance')
 def test_missing_private_signals_stay_unknown(self):
  self.assertTrue(all(s['state']=='UNKNOWN' for s in signals({}) if s['source_field']=='PrivateRemarks'))
 def test_future_outcomes_not_in_structured_features(self):
  self.assertFalse(set(STRUCTURED)&{'ClosePrice','CloseDate','PurchaseContractDate','ListingKey','ParcelNumber','UnparsedAddress'})
 def test_unknown_fields_cannot_be_written(self):
  a=answer();a['human_confirmed']=True
  with self.assertRaises(ValueError):validate(a)
if __name__=='__main__':unittest.main()

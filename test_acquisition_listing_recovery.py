import unittest
from acquisition_listing_recovery import acquisition_candidates
class AcquisitionChronology(unittest.TestCase):
 def setUp(self):
  self.source={'apn':'123-456-78-00','address':'123 Main St','unit':'2','prior_date':'2025-01-01','prior_recording_date':'2025-01-15','last_date':'2026-06-01'}
  self.before={'ListingKey':'before','ParcelNumber':'1234567800','StreetNumber':'123','UnitNumber':'2','CloseDate':'2025-01-15'}
 def test_recovers_old_record_and_rejects_resale(self):
  after={**self.before,'ListingKey':'after','CloseDate':'2026-06-01'}
  self.assertEqual(acquisition_candidates([after,self.before],self.source),[self.before])
 def test_rejects_wrong_unit_and_parcel(self):
  for changes in [{'UnitNumber':'3'},{'ParcelNumber':'1234567801'},{'StreetNumber':'124'}]:
   self.assertEqual(acquisition_candidates([{**self.before,**changes}],self.source),[])
 def test_reports_multiple_candidates_without_guessing(self):
  self.assertEqual(len(acquisition_candidates([self.before,{**self.before,'ListingKey':'other'}],self.source)),2)
if __name__=='__main__':unittest.main()

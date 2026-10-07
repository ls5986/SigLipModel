import unittest
from copy import deepcopy
from target_review import validate_answer

class ReviewValidation(unittest.TestCase):
    def setUp(self):
        self.answer={'group_id':'00000000-0000-0000-0000-000000000001','expected_revision':0,'overall':'TARGET','image':{'decision':'NOT_TARGET','strength':None},'metadata':{'decision':'TARGET','strength':75},'notes':''}
    def test_independent_decisions(self):
        answer,revision=validate_answer(self.answer)
        self.assertEqual(answer['image']['decision'],'NOT_TARGET')
        self.assertEqual(answer['metadata']['strength'],75)
        self.assertEqual(answer['overall'],'TARGET')
    def test_bounds_and_types(self):
        for value in [49,101,True,75.5,None]:
            with self.subTest(value=value):
                self.answer['metadata']['strength']=value
                with self.assertRaises(ValueError): validate_answer(self.answer)
    def test_non_target_has_no_score(self):
        self.answer['image']['strength']=50
        with self.assertRaises(ValueError): validate_answer(self.answer)
    def test_unknown_and_extra_fields(self):
        self.answer['image']={'decision':'INSUFFICIENT_EVIDENCE','strength':None}
        validate_answer(self.answer)
        self.answer['reviewer']='spoof'
        with self.assertRaises(ValueError): validate_answer(self.answer)

if __name__=='__main__': unittest.main()

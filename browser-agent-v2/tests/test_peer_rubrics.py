import unittest
from v2.peer_rubrics import parse_requirements,validate_review_draft
from v2.peer import review_status
from v2.handlers import HandlerError
class RubricTests(unittest.TestCase):
 def test_requirements_not_peer_count(self):
  r=parse_requirements('Word limit: 500 Grading Criteria Overview graded by two peers. Did the learner cite the study? Did the learner summarize clearly?')
  self.assertEqual(r['word_limit'],500);self.assertEqual(len(r['criteria']),2)
  self.assertEqual(review_status('graded by two peers')['remaining'],None)
 def test_no_rubric_is_not_success(self):
  self.assertEqual(parse_requirements('Loading...')['criteria'],[])
  with self.assertRaises(HandlerError):validate_review_draft({},None,[])
 def test_wrong_submission_and_missing_evidence_blocked(self):
  rubric=parse_requirements('Grading Criteria Overview Did the learner cite the study?')['criteria']
  row={'id':rubric[0]['id'],'choice':'Yes','evidence':'The work cites the paper URL.'}
  d={'submission_id':'A','criteria':[row]}
  self.assertEqual(validate_review_draft(d,'A',rubric),d)
  with self.assertRaises(HandlerError):validate_review_draft(d,'B',rubric)
  row['evidence']=''
  with self.assertRaises(HandlerError):validate_review_draft(d,'A',rubric)
 def test_prerequisite_and_explicit_count(self):
  self.assertEqual(review_status('Submit your assignment before you can review. 3 reviews left to complete'),{'state':'submission_required','remaining':3})

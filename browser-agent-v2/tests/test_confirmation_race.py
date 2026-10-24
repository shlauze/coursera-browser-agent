import unittest
from unittest.mock import Mock
from v2.assessments import _confirmation_observed
class ConfirmationRaceTests(unittest.TestCase):
 def test_rerender_settles_without_click(self):
  page=Mock();page.evaluate.side_effect=[{"attestation":True},{"attestation":True},{"attestation":False}]
  self.assertTrue(_confirmation_observed(page,Mock()))
  self.assertEqual(page.wait_for_timeout.call_count,2)
  for call in page.evaluate.call_args_list:self.assertEqual(call.args[1],{"action":"inspect", "allow_random_duplicates":False, "visual_reviews":[]})
 def test_unchecked_confirmation_stays_blocked(self):
  page=Mock();page.evaluate.return_value={"attestation":True}
  self.assertFalse(_confirmation_observed(page,Mock()))
 def test_wrong_page_is_not_confirmation(self):
  page=Mock();page.evaluate.return_value={"error":"Not a recognized assignment page"}
  self.assertFalse(_confirmation_observed(page,Mock()))
 def test_already_checked_does_not_wait(self):
  page=Mock();page.evaluate.return_value={"attestation":False}
  self.assertTrue(_confirmation_observed(page,Mock()))
  page.wait_for_timeout.assert_not_called()

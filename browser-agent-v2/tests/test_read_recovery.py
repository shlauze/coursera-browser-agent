import unittest
from unittest.mock import Mock
from playwright.sync_api import Error, TimeoutError
from v2.recovery import read_retry
from v2.outline import OutlineNotSettled
from v2.ui import UnknownOverlay
class ReadRecoveryTests(unittest.TestCase):
 def test_transient_retries_then_returns(self):
  for error in [TimeoutError("load"),Error("Execution context was destroyed"),OutlineNotSettled("loading")]:
   page=Mock();operation=Mock(side_effect=[error,42])
   self.assertEqual(read_retry(operation)(page),42)
   self.assertEqual(operation.call_count,2);page.reload.assert_not_called()
 def test_three_attempt_bound(self):
  operation=Mock(side_effect=TimeoutError("load"))
  with self.assertRaises(TimeoutError):read_retry(operation)(Mock())
  self.assertEqual(operation.call_count,3)
 def test_unknown_modal_and_closed_page_are_not_retried(self):
  for error in [UnknownOverlay("terms"),Error("Target page has been closed"),ValueError("ambiguity")]:
   operation=Mock(side_effect=error)
   with self.assertRaises(type(error)):read_retry(operation)(Mock())
   self.assertEqual(operation.call_count,1)

 def test_outline_root_reacquired_after_react_replacement(self):
  from unittest.mock import patch
  from v2.outline import scan_outline, _ITEMS_JS
  page=Mock(url="https://www.coursera.org/learn/example/lecture/a")
  checks=iter([False,True,True])
  row={"title":"Exam","url":"https://www.coursera.org/learn/example/quiz/a","kind":"Graded Assignment","completed":False}
  def evaluate(script):
   if script==_ITEMS_JS:return {"title":"Course","items":[row]}
   return next(checks)
  page.evaluate.side_effect=evaluate
  with patch('v2.outline._find_sidebar') as find,patch('v2.outline._expand_modules'):
   result=scan_outline(page)
  self.assertEqual(find.call_count,2)
  self.assertEqual(len(result.items),1)
  self.assertFalse(result.items[0].completed)

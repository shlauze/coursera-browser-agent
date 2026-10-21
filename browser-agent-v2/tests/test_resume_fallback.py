import unittest
from unittest.mock import Mock
from v2.discovery import CourseCard, DiscoveryError, _open_verified_course_home
class ResumeFallbackTests(unittest.TestCase):
 def card(self,href):
  return CourseCard("Target","Resume",0,1,0,course_href=href)
 def test_uses_live_same_course_home_without_submission(self):
  page=Mock();opened=page.context.new_page.return_value
  opened.url="https://www.coursera.org/learn/target/home/module/1"
  self.assertIs(_open_verified_course_home(page,self.card("https://www.coursera.org/learn/target/home/welcome")),opened)
  opened.goto.assert_called_once_with("https://www.coursera.org/learn/target/home/welcome",wait_until="domcontentloaded",timeout=45000)
  page.goto.assert_not_called();page.close.assert_not_called()
 def test_foreign_card_link_rejected(self):
  page=Mock()
  with self.assertRaises(DiscoveryError):_open_verified_course_home(page,self.card("https://example.org/learn/target"))
  page.context.new_page.assert_not_called()
 def test_wrong_course_redirect_rejected(self):
  page=Mock();page.context.new_page.return_value.url="https://www.coursera.org/learn/other/home/welcome"
  with self.assertRaises(DiscoveryError):_open_verified_course_home(page,self.card("https://www.coursera.org/learn/target"))
  page.context.new_page.return_value.close.assert_called_once()

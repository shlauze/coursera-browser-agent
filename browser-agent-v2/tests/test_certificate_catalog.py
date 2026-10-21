import unittest
from unittest.mock import Mock,patch
from v2.runner import _certificate_in_catalog,_verified_certificate_url
URL='https://www.coursera.org/account/accomplishments/verify/ABC'
class CatalogTests(unittest.TestCase):
 def setup_pages(self):
  page=Mock(url='https://www.coursera.org/learn/course/home/welcome');catalog=Mock();cert=Mock(url=URL)
  page.context.new_page.side_effect=[catalog,cert]
  catalog.get_by_role.return_value.count.return_value=2
  catalog.get_by_role.return_value.last.is_enabled.return_value=False
  return page,catalog,cert
 def test_catalog_used_when_home_has_no_link(self):
  p,_,_=self.setup_pages()
  with patch('v2.runner._course_certificate_url',return_value=None),patch('v2.runner._certificate_in_catalog',return_value=URL) as search:
   self.assertEqual(_verified_certificate_url(p,'Course'),URL)
   search.assert_called_once_with(p,'Course')
 def test_second_page_exact_verified_and_owned_tabs_closed(self):
  p,c,v=self.setup_pages();c.evaluate.side_effect=[{'signature':'first','urls':[]},{'signature':'second','urls':[URL]}]
  c.get_by_role.return_value.last.is_enabled.return_value=True
  with patch('v2.runner._wait_certificate',return_value=True):self.assertEqual(_certificate_in_catalog(p,'Course'),URL)
  c.get_by_role.return_value.last.click.assert_called_once();c.close.assert_called_once();v.close.assert_called_once();p.close.assert_not_called();p.goto.assert_not_called()
 def test_wrong_certificate_is_not_completion(self):
  p,c,v=self.setup_pages();c.evaluate.return_value={'signature':'one','urls':[URL]}
  with patch('v2.runner._wait_certificate',return_value=False):self.assertIsNone(_certificate_in_catalog(p,'Course'))
  v.close.assert_called_once();c.close.assert_called_once()
 def test_stuck_pagination_is_bounded(self):
  p,c,v=self.setup_pages();c.evaluate.return_value={'signature':'one','urls':[]};c.get_by_role.return_value.last.is_enabled.return_value=True
  self.assertIsNone(_certificate_in_catalog(p,'Course'))
  c.get_by_role.return_value.last.click.assert_called_once();c.close.assert_called_once()
 def test_foreign_candidate_never_opened(self):
  p,c,v=self.setup_pages();c.evaluate.return_value={'signature':'one','urls':['https://evil.test/account/accomplishments/verify/ABC']}
  self.assertIsNone(_certificate_in_catalog(p,'Course'));self.assertEqual(p.context.new_page.call_count,1)
 def test_navigation_error_closes_only_owned_tab(self):
  p,c,v=self.setup_pages();c.goto.side_effect=RuntimeError('offline')
  with self.assertRaises(RuntimeError):_certificate_in_catalog(p,'Course')
  c.close.assert_called_once();p.close.assert_not_called()

import unittest
from unittest.mock import Mock, patch
from v2.runner import _verified_certificate_url, _wait_certificate

class CertificateRecoveryTests(unittest.TestCase):
    def test_last_assessment_falls_back_to_home_and_preserves_original_tab(self):
        page = Mock(url="https://www.coursera.org/learn/example/assignment-submission/id/view-feedback")
        home = page.context.new_page.return_value
        with patch("v2.runner._certificate_on_page", side_effect=[None, None, "https://www.coursera.org/account/accomplishments/verify/ABC"]):
            result = _verified_certificate_url(page, "Exact Course")
        self.assertTrue(result.endswith("/ABC"))
        home.goto.assert_called_once_with("https://www.coursera.org/learn/example/home/welcome", wait_until="domcontentloaded", timeout=30000)
        home.close.assert_called_once()
        page.goto.assert_not_called()
        page.close.assert_not_called()

    def test_missing_certificate_is_not_completion(self):
        page = Mock(url="https://www.coursera.org/learn/example/home/welcome")
        with patch("v2.runner._certificate_on_page", return_value=None), patch("v2.runner._certificate_in_catalog", return_value=None):
            self.assertIsNone(_verified_certificate_url(page,"Exact Course"))
        page.context.new_page.return_value.close.assert_called_once()

    def test_waits_for_hydrated_exact_certificate(self):
        page = Mock()
        with patch("v2.runner._certificate_matches", side_effect=[False,False,True]):
            self.assertTrue(_wait_certificate(page,"Exact Course"))
        self.assertEqual(page.wait_for_timeout.call_count,2)

    def test_foreign_origin_never_used_as_course_home(self):
        page = Mock(url="https://example.org/learn/example/home/welcome")
        with patch("v2.runner._certificate_on_page",return_value=None):
            self.assertIsNone(_verified_certificate_url(page,"Exact Course"))
        page.context.new_page.assert_not_called()

    def test_home_redirect_during_read_is_retried(self):
        from playwright.sync_api import Error
        page = Mock(url="https://www.coursera.org/learn/example/assignment-submission/id")
        with patch("v2.runner._certificate_on_page",side_effect=[None,Error("Execution context was destroyed"),"https://www.coursera.org/account/accomplishments/verify/ABC"]):
            self.assertTrue(_verified_certificate_url(page,"Exact Course").endswith("ABC"))
        page.context.new_page.return_value.close.assert_called_once()

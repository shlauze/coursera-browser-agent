import unittest
from unittest.mock import Mock, patch
from v2.runner import Runner, StopRequested
from v2.ui import UnknownOverlay

class OverlayWaitTests(unittest.TestCase):
    def runner(self):
        r=object.__new__(Runner)
        r.store=Mock(); r.control=Mock(); r.checkpoint=Mock()
        return r
    def test_disappearance_continues_without_global_resume(self):
        r=self.runner(); p=Mock(url="https://www.coursera.org/learn/example")
        with patch("v2.ui.require_clear_outline",side_effect=[UnknownOverlay("modal"),None]):
            r._wait_for_overlay(p)
        self.assertEqual(r.checkpoint.call_count,2)
        r.control.request_resume.assert_not_called()
        r.control.request_pause.assert_not_called()
        p.wait_for_timeout.assert_called_once_with(1000)
        r.store.set_meta.assert_called_with("confirmation_required",None)
    def test_stop_prevents_further_page_reads(self):
        r=self.runner();r.checkpoint.side_effect=StopRequested()
        with patch("v2.ui.require_clear_outline") as read:
            with self.assertRaises(StopRequested):r._wait_for_overlay(Mock())
        read.assert_not_called()
        r.store.set_meta.assert_called_with("confirmation_required",None)

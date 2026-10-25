"""Regression for slow CDP attachment without changing existing browser tabs."""
import unittest
from unittest.mock import MagicMock, patch

from playwright.sync_api import TimeoutError
from v2.browser import BrowserSession, MY_LEARNING_URL


class BrowserAttachTests(unittest.TestCase):
    def test_slow_existing_browser_attaches_once_and_preserves_user_tabs(self):
        driver = MagicMock()
        browser = MagicMock()
        context = browser.contexts.__getitem__.return_value
        existing_page = MagicMock()
        context.pages = [existing_page]

        def handshake(endpoint, *, timeout):
            if timeout < 45700:
                raise TimeoutError('Target attachment took 45.7 seconds')
            return browser

        driver.chromium.connect_over_cdp.side_effect = handshake
        with patch('v2.browser._devtools_ready', return_value=True), patch('v2.browser.subprocess.Popen') as launch, patch('playwright.sync_api.sync_playwright') as factory:
            factory.return_value.start.return_value = driver
            session = BrowserSession('profile')
            session.connect()
            session.connect()
            self.assertEqual(driver.chromium.connect_over_cdp.call_count, 1)
            launch.assert_not_called()
            context.new_page.assert_called_once()
            context.new_page.return_value.goto.assert_called_once_with(MY_LEARNING_URL, wait_until='domcontentloaded', timeout=45000)
            existing_page.goto.assert_not_called()
            session.close()
            existing_page.close.assert_not_called()
            browser.close.assert_not_called()
            driver.stop.assert_called_once()

    def test_attach_failure_stops_driver_without_relaunch_or_browser_close(self):
        driver = MagicMock()
        driver.chromium.connect_over_cdp.side_effect = TimeoutError('Still unavailable')
        with patch('v2.browser._devtools_ready', return_value=True), patch('v2.browser.subprocess.Popen') as launch, patch('playwright.sync_api.sync_playwright') as factory:
            factory.return_value.start.return_value = driver
            session = BrowserSession('profile')
            with self.assertRaises(TimeoutError):
                session.connect()
            self.assertEqual(driver.chromium.connect_over_cdp.call_count, 1)
            driver.stop.assert_called_once()
            launch.assert_not_called()
            self.assertIsNone(session.page)

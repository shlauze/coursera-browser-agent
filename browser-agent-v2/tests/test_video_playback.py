"""Real browser/media regressions for Coursera's overlapping player controls."""
import base64
import io
import unittest
import wave
from unittest.mock import Mock

from playwright.sync_api import sync_playwright
from v2.handlers import HandlerError, handle_video


class VideoPlaybackTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pw = sync_playwright().start()
        cls.browser = cls.pw.chromium.launch(channel="chrome", headless=True)
        data = io.BytesIO()
        with wave.open(data, "wb") as wav:
            wav.setnchannels(1)
            wav.setsampwidth(2)
            wav.setframerate(8000)
            wav.writeframes(b"\x00\x00" * 16000)
        cls.media = "data:audio/wav;base64," + base64.b64encode(data.getvalue()).decode()

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.pw.stop()

    def setUp(self):
        self.page = self.browser.new_page()
        self.addCleanup(self.page.close)
        self.page.set_content(f'''<div style="position:relative;width:320px;height:180px">
          <video style="width:320px;height:180px" src="{self.media}"></video>
          <div style="position:absolute;inset:0;background:#333" id="controls">
            <button id="play" onclick="document.querySelector('video').play()">Play</button>
          </div></div>''')

    def test_overlay_does_not_prevent_real_media_finishing(self):
        self.page.locator("#controls").evaluate("e => e.onclick=()=>{throw Error('Unexpected overlay click')}")
        store = Mock()
        result = handle_video(self.page, lambda: None, max_seconds=10, store=store)
        self.assertIn("end", result.message)
        self.assertTrue(self.page.locator("video").evaluate("v=>v.ended && v.currentTime >= 2"))
        events = store.record_event.call_args_list
        self.assertEqual(events[-1].args[1], "VIDEO_PROGRESS")
        self.assertTrue(events[-1].args[3]["ended"])
        self.assertGreaterEqual(len(events), 2)

    def test_already_playing_media_is_not_toggled_or_restarted(self):
        self.page.locator("video").evaluate("v => v.play()")
        self.page.locator("video").evaluate("v => v.play=()=>{throw Error('Unexpected play call')}")
        handle_video(self.page, lambda: None, max_seconds=10)
        self.assertTrue(self.page.locator("video").evaluate("v=>v.ended"))

    def test_autoplay_denial_uses_visible_play_control(self):
        self.page.evaluate('''() => {
          const v=document.querySelector('video'); const original=v.play.bind(v);
          v.play=()=>Promise.reject(new DOMException('Gesture required','NotAllowedError'));
          document.querySelector('#play').onclick=()=>{document.body.dataset.clicked='yes'; original();};
        }''')
        handle_video(self.page, lambda: None, max_seconds=10)
        self.assertEqual(self.page.locator("body").get_attribute("data-clicked"), "yes")
        self.assertTrue(self.page.locator("video").evaluate("v=>v.ended"))

    def test_media_error_is_not_reported_as_complete(self):
        self.page.locator("video").evaluate("v=>v.play=()=>Promise.reject(new DOMException('Unsupported media','NotSupportedError'))")
        with self.assertRaisesRegex(HandlerError, "NotSupportedError"):
            handle_video(self.page, lambda: None, max_seconds=10)

    def test_coursework_mode_pauses_lecture_even_when_player_starts_later(self):
        from v2.runner import _pause_lecture_media
        html = self.page.content()
        url = 'https://www.coursera.org/learn/demo/lecture/example'
        self.page.route(url, lambda route: route.fulfill(body=html))
        self.page.goto(url)
        _pause_lecture_media(self.page)
        self.page.locator('video').evaluate('v=>v.play().catch(()=>{})')
        self.page.wait_for_timeout(300)
        self.assertTrue(self.page.locator('video').evaluate('v=>v.paused && !v.ended'))

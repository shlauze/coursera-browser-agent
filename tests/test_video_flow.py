import unittest

from video_flow import complete_video, same_lecture_page
from coursera_start import StopRun


VIDEO_URL = "https://www.coursera.org/learn/deep-learning-with-pytorch/lecture/abc123/lesson"
LAB_URL = "https://www.coursera.org/learn/deep-learning-with-pytorch/ungradedLab/VZXic/lab"
ITEM = {"url": VIDEO_URL, "key": "lecture:abc123", "title": "Test video Video • 6 min"}


class FakeTab:
    def __init__(self, results, runner):
        self.results = iter(results)
        self.runner = runner

    async def evaluate(self, expression):
        self.runner.actions += 1
        result = next(self.results)
        self.runner.green = bool(result.get("green"))
        return result


class FakeRunner:
    def __init__(self, results):
        self.opens = 0
        self.actions = 0
        self.green = False
        self.events = []
        self.tab = FakeTab(results, self)

    async def open_item(self, item):
        self.opens += 1

    async def verified_item(self, item, wait=0):
        return self.green

    def event(self, name, **fields):
        self.events.append((name, fields))


class VideoFlowTests(unittest.IsolatedAsyncioTestCase):
    def test_page_identity_is_exact(self):
        self.assertTrue(same_lecture_page(VIDEO_URL + "/", VIDEO_URL))
        self.assertFalse(same_lecture_page(LAB_URL, VIDEO_URL))
        self.assertFalse(same_lecture_page("https://other.example/learn/deep-learning-with-pytorch/lecture/abc123/lesson", VIDEO_URL))

    async def test_redirect_during_player_action_reopens_target_lesson(self):
        runner = FakeRunner([
            {"url": LAB_URL, "ended": False, "current_time": 0, "duration": None},
            {"url": VIDEO_URL, "ended": True, "green": True},
        ])
        await complete_video(runner, ITEM, page_attempts=2, probe_delay=0)
        self.assertEqual(runner.opens, 2)
        self.assertEqual(runner.actions, 2)
        self.assertEqual(len([e for e, _ in runner.events if e == "video_page_drift"]), 1)

    async def test_wrong_page_error_reopens_without_exhausting_media_probes(self):
        runner = FakeRunner([
            {"error": "Wrong video page; no playback changed.", "url": LAB_URL},
            {"url": VIDEO_URL, "ended": True, "green": True},
        ])
        await complete_video(runner, ITEM, page_attempts=2, probe_delay=0)
        self.assertEqual(runner.opens, 2)
        self.assertEqual(runner.actions, 2)

    async def test_player_end_without_green_mark_remains_unconfirmed(self):
        runner = FakeRunner([
            {"url": VIDEO_URL, "ended": True},
            {"url": VIDEO_URL, "ended": True},
        ])
        with self.assertRaisesRegex(StopRun, "not confirmed"):
            await complete_video(runner, ITEM, page_attempts=2, probe_delay=0)
        self.assertFalse(runner.green)
        self.assertEqual(runner.opens, 2)


if __name__ == "__main__":
    unittest.main()

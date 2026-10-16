"""Offline reader regressions for the two audited AI Governance process carousels."""

import json
from unittest import IsolatedAsyncioTestCase
from unittest.mock import patch

from coursera_start import StopRun
import rise_scorm_flow as flow
from tests.test_rise_scorm_flow import COURSE, InnerTab, OuterTab, action


ITEM = {
    "key": "ungradedWidget:A92vu",
    "title": "AI Governance Ungraded Plugin • 15 min Resume",
    "url": COURSE + "/ungradedWidget/A92vu/ai-governance",
}
FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "4b5023fcb65848969e37548857aa463d/index.html"
)
LESSON = "hY_U7ZBg5cNykCeSR--uow6iD46hTjua"
LESSON_URL = FRAME.replace("index.html", "") + (
    "scorm-content/scormcontent/index.html#/lessons/" + LESSON
)
HEADINGS = ("AI Governance Frameworks", "AI Governance Tools")


class ProcessInner(InnerTab):
    def __init__(self, active=(1, 1), *, stuck=False, corrupt=False):
        super().__init__([{"accordions": 0, "cards": 0,
                           "max": 0, "continue": False}])
        self.active = list(active)
        self.visited = [{index} for index in active]
        self.clicks = []
        self.stuck = stuck
        self.corrupt = corrupt

    def groups(self):
        groups = []
        for group_index, heading in enumerate(HEADINGS):
            contents = [
                f"{heading} slide {slide_index} gives a detailed static "
                f"explanation of AI governance policies and examples. " * 3
                for slide_index in range(1, 7)
            ]
            groups.append({
                "heading": heading, "contents": contents,
                "active_index": self.active[group_index],
                "signature": json.dumps([heading, *contents]),
            })
        if self.corrupt:
            groups[1]["signature"] = json.dumps(["changed"])
        return groups

    async def evaluate(self, expression):
        operation = action(expression)
        if operation == "process_carousel_dot":
            request = json.loads(expression[len(flow.INNER_JS) + 1:-1])
            group_index = request["process_carousel_index"] - 1
            slide_index = request["process_slide_index"]
            assert request["process_carousel_signature"] == (
                self.groups()[group_index]["signature"])
            self.clicks.append((group_index + 1, slide_index))
            if not self.stuck:
                self.active[group_index] = slide_index
                self.visited[group_index].add(slide_index)
        snapshot = await super().evaluate(expression)
        snapshot.update({
            "url": FRAME, "lesson": LESSON, "lesson_url": LESSON_URL,
            "process_carousels": self.groups(),
        })
        return snapshot


class ProcessOuter(OuterTab):
    def __init__(self, inner):
        super().__init__()
        self.inner = inner

    async def evaluate(self, expression):
        if action(expression) == "mark":
            assert self.inner.visited == [set(range(1, 7)), set(range(1, 7))]
        result = await super().evaluate(expression)
        result.update({"url": ITEM["url"], "frame": FRAME,
                       "frame_title": "AI Governance"})
        return result


class Runner:
    def __init__(self, inner):
        self.course = {"url": COURSE}
        self.tab = ProcessOuter(inner)
        self.events = []

    async def verified_item(self, item, wait=0):
        assert item is ITEM
        return self.tab.marked

    def event(self, name, **fields):
        self.events.append((name, fields))


class ProcessCarouselFlowTests(IsolatedAsyncioTestCase):
    async def run_flow(self, inner, **kwargs):
        runner = Runner(inner)
        with patch.object(flow, "open_scorm_frame", return_value=inner) as opened:
            await flow.complete_rise_scorm(
                runner, ITEM, settle_delay=0, terminal_delay=.001, **kwargs)
            opened.assert_awaited_once_with(runner.tab, FRAME)
        return runner

    async def test_reads_both_process_carousels_before_mark(self):
        inner = ProcessInner()
        runner = await self.run_flow(inner)
        self.assertEqual(inner.visited, [set(range(1, 7)), set(range(1, 7))])
        self.assertEqual(inner.clicks, [(group, slide)
                                        for group in (1, 2)
                                        for slide in range(2, 7)])
        self.assertTrue(runner.tab.marked)
        self.assertTrue(inner.closed)

    async def test_resume_visits_other_slides_in_both_groups(self):
        inner = ProcessInner(active=(4, 2))
        runner = await self.run_flow(inner)
        self.assertEqual(inner.visited, [set(range(1, 7)), set(range(1, 7))])
        self.assertNotIn((1, 4), inner.clicks)
        self.assertNotIn((2, 2), inner.clicks)
        self.assertTrue(runner.tab.marked)

    async def test_stuck_process_slide_never_marks(self):
        inner = ProcessInner(stuck=True)
        runner = Runner(inner)
        with patch.object(flow, "open_scorm_frame", return_value=inner):
            with self.assertRaisesRegex(StopRun, "did not advance"):
                await flow.complete_rise_scorm(
                    runner, ITEM, settle_delay=0, terminal_delay=.001)
        self.assertFalse(runner.tab.marked)
        self.assertTrue(inner.closed)

    async def test_changed_group_signature_fails_closed(self):
        inner = ProcessInner(corrupt=True)
        runner = Runner(inner)
        with patch.object(flow, "open_scorm_frame", return_value=inner):
            with self.assertRaisesRegex(StopRun, "changed unexpectedly"):
                await flow.complete_rise_scorm(
                    runner, ITEM, settle_delay=0, terminal_delay=.001)
        self.assertFalse(runner.tab.marked)

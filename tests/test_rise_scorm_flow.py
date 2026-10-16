"""Offline tests for the generic Rise reader; no browser is contacted."""

import importlib
import json
import re
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
flow = importlib.import_module("rise_scorm_flow")
plugins = importlib.import_module("plugin_flow")
audited = importlib.import_module("scorm_flow")
StopRun = importlib.import_module("coursera_start").StopRun
COURSE = (
    "https://www.coursera.org/learn/"
    "adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
)
ITEM = {
    "key": "ungradedWidget:gTEoB",
    "title": "Defence techniques and strategies Ungraded Plugin • 30 min",
    "url": COURSE + "/ungradedWidget/gTEoB/defence-techniques-and-strategies",
}
FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "7593944453f8458fb0f5c677ef10dc9d/index.html"
)
LESSON = "eih4-kXzAFPtZWonw92N1bgBl3WCi9fa"
LESSON_URL = FRAME.replace("index.html", "") + (
    "scorm-content/scormcontent/index.html#/lessons/" + LESSON
)


def action(expression):
    for name in ("inspect", "expand", "flip", "carousel_dot",
                 "process_carousel_dot", "tab_select",
                 "checklist_check",
                 "hotspot_open", "hotspot_close", "scroll", "continue", "mark"):
        if '"action": "' + name + '"' in expression:
            return name
    raise AssertionError("missing action in evaluated script")


class OuterTab:
    def __init__(self, *, wrong_frame=False, no_green=False):
        self.calls = []
        self.marked = False
        self.wrong_frame = wrong_frame
        self.no_green = no_green

    async def evaluate(self, expression):
        self.calls.append(action(expression))
        result = {
            "url": ITEM["url"], "ready": "complete",
            "frame": FRAME if not self.wrong_frame else "https://elsewhere.invalid/index.html",
            "frame_title": "Defence techniques and strategies",
            "mark_count": 1, "mark_disabled": False, "modal": False, "other": [],
        }
        if action(expression) == "mark":
            self.marked = True
            result["clicked"] = "mark"
        return result


class InnerTab:
    def __init__(self, stages, *, error=None, stuck_continue=False,
                 exclusive=False, rerender_ids=False, transition_gap=False,
                 changed_first_panel=False):
        self.stages = stages
        self.stage = 0
        self.expanded = set()
        self.flipped = 0
        self.top = 0
        self.error = error
        self.stuck_continue = stuck_continue
        self.exclusive = exclusive
        self.rerender_ids = rerender_ids
        self.generation = 0
        self.transition_gap = transition_gap
        self.gap_remaining = 0
        self.changed_first_panel = changed_first_panel
        self.panel_versions = {}
        self.calls = []
        self.closed = False

    async def evaluate(self, expression):
        operation = action(expression)
        self.calls.append(operation)
        if self.error:
            return {"error": self.error}
        if operation == "inspect" and self.gap_remaining:
            self.gap_remaining -= 1
            if not self.gap_remaining:
                self.stage += 1
        spec = self.stages[self.stage]
        if operation == "expand":
            match = re.search(r'"expand_id": "panel-(\d+)-(\d+)"', expression)
            assert match, "expand must identify one current accordion"
            assert int(match[1]) == self.generation
            index = int(match[2])
            self.expanded = {index} if self.exclusive else self.expanded | {index}
            if self.changed_first_panel and index == 1:
                self.panel_versions[0] = 2
            if self.rerender_ids:
                self.generation += 1
        elif operation == "flip":
            self.flipped += 1
        elif operation == "scroll":
            self.top = min(spec["max"], self.top + 500)
        elif operation == "continue" and not self.stuck_continue:
            self.stage += 1
            self.expanded = set()
            self.flipped = self.top = 0
            if self.transition_gap:
                self.gap_remaining = 2
        accordions = [
            {
                "id": f"panel-{self.generation}-{index}",
                "heading": f"Panel {index}",
                "content": (
                    f"Static explanation for panel {index} with sufficient detail "
                    f"version {self.panel_versions.get(index, 1)}."
                ),
                "expanded": index in self.expanded,
            }
            for index in range(self.stages[self.stage]["accordions"])
        ]
        return {
            "url": FRAME, "lesson_url": LESSON_URL, "lesson": LESSON,
            "progress": "0% COMPLETE", "text_length": 1000 + self.stage * 100,
            "signature": f"stage-{self.stage}", "top": self.top,
            "max": self.stages[self.stage]["max"],
            "accordions": accordions,
            "accordion_signature": f"stage-{self.stage}-generation-{self.generation}",
            "accordion_count": self.stages[self.stage]["accordions"],
            "expanded_count": len(self.expanded),
            "card_count": self.stages[self.stage]["cards"],
            "flipped_count": self.flipped,
            "continue_count": int(self.stages[self.stage]["continue"]),
            "carousel_count": 0, "carousel_active_index": 0,
            "carousel_contents": [], "carousel_signature": "[]",
            "tab_count": 0, "tab_active_index": 0,
            "tabs": [], "tab_signature": "[]",
            "hotspot_count": 0, "hotspot_markers": [],
            "hotspot_signature": "[]", "hotspot_viewed_count": 0,
            "hotspot_open_index": 0,
            "checklist_count": 0, "checklist_checked_count": 0,
            "checklist": [], "checklist_signature": "[]",
            "clicked": operation if operation != "inspect" else None,
        }

    async def close(self):
        self.closed = True


class CarouselInnerTab(InnerTab):
    CONTENTS = [
        "Evaluating Security Risk in DeepSeek and Other Frontier Reasoning Models. Cisco Blogs.",
        "Hacking Google Bard - From Prompt Injection to Data Exfiltration. Wunderwuzzi's blog.",
        "Azure AI announces Prompt Shields for Jailbreak and Indirect prompt injection attacks.",
        "Lessons learned from ChatGPT's Samsung leak. Cybernews.com.",
        "Microsoft's RAG Copilot can be tricked into leaking enterprise secrets. The Stack.",
    ]

    def __init__(self, *, active=1, stuck=False):
        super().__init__([{
            "accordions": 0, "cards": 0, "max": 0, "continue": False,
        }])
        self.carousel_active_index = active
        self.carousel_visited = {active}
        self.carousel_clicks = []
        self.stuck = stuck

    async def evaluate(self, expression):
        if action(expression) == "carousel_dot":
            match = re.search(r'"carousel_index": (\d+)', expression)
            assert match, "carousel action must identify an audited slide"
            index = int(match[1])
            self.carousel_clicks.append(index)
            if not self.stuck:
                self.carousel_active_index = index
                self.carousel_visited.add(index)
        snapshot = await super().evaluate(expression)
        snapshot.update({
            "carousel_count": 1,
            "carousel_active_index": self.carousel_active_index,
            "carousel_contents": self.CONTENTS[:],
            "carousel_signature": json.dumps(self.CONTENTS),
        })
        return snapshot


class StaticTabsInnerTab(InnerTab):
    TABS = [
        {"label": "GENDER CLASSIFICATION SYSTEMS",
         "content": "Several sensitive AI applications are riddled with examples of biased behaviour. " * 2},
        {"label": "RECIDIVISM PREDICTION TOOL",
         "content": "Fairness bias in AI models stems from biased training data and design. " * 2},
    ]

    def __init__(self, *, active=1, stuck=False):
        super().__init__([{
            "accordions": 0, "cards": 0, "max": 0, "continue": False,
        }])
        self.active = active
        self.visited = {active}
        self.stuck = stuck
        self.tab_clicks = []

    async def evaluate(self, expression):
        if action(expression) == "tab_select":
            request = json.loads(expression[len(flow.INNER_JS) + 1:-1])
            assert request["tab_signature"] == json.dumps([
                [entry["label"], entry["content"]] for entry in self.TABS
            ])
            index = request["tab_index"]
            self.tab_clicks.append(index)
            if not self.stuck:
                self.active = index
                self.visited.add(index)
        snapshot = await super().evaluate(expression)
        snapshot.update({
            "tab_count": 2,
            "tab_active_index": self.active,
            "tabs": self.TABS,
            "tab_signature": json.dumps([
                [entry["label"], entry["content"]] for entry in self.TABS
            ]),
        })
        return snapshot


class HotspotInnerTab(InnerTab):
    LABELS = [
        "Marker, Blue team (defensive AI), Right arrow",
        "Marker, Red team (offensive AI), Left arrow",
        "Marker, Purple team, Up arrow",
    ]
    CONTENTS = [
        f"{label.split(', ')[1]} explains the static defensive reading in detail. " * 3
        for label in LABELS
    ]

    def __init__(self, *, viewed=False, stuck=None, plus=False,
                 viewed_on_close=False):
        super().__init__([{
            "accordions": 4, "cards": 0, "max": 600, "continue": False,
        }])
        self.viewed = set(range(1, len(self.LABELS) + 1)) if viewed else set()
        self.open_index = 0
        self.stuck = stuck
        self.plus = plus
        self.viewed_on_close = viewed_on_close
        self.hotspot_clicks = []

    def current_labels(self):
        if not self.plus:
            return self.LABELS
        return [
            "Marker, " + label.split(", ")[1] +
            (", Minus" if index == self.open_index else ", Plus")
            for index, label in enumerate(self.LABELS, start=1)
        ]

    def current_signature(self):
        return json.dumps([
            [f"bubble-{index}", label, content]
            for index, (label, content) in enumerate(
                zip(self.current_labels(), self.CONTENTS), start=1)
        ])

    async def evaluate(self, expression):
        operation = action(expression)
        if operation not in ("inspect", "hotspot_open", "hotspot_close"):
            assert self.viewed == set(range(1, len(self.LABELS) + 1)) and self.open_index == 0, (
                "Reading controls were used before all graphic markers were closed"
            )
        if operation in ("hotspot_open", "hotspot_close"):
            match = re.search(r'"hotspot_index": (\d+)', expression)
            assert match, "hotspot action must identify an audited marker"
            index = int(match[1])
            request = json.loads(expression[len(flow.INNER_JS) + 1:-1])
            assert request["hotspot_signature"] == self.current_signature(), (
                "static marker identity must be sent"
            )
            self.hotspot_clicks.append((operation, index))
            if self.stuck != operation:
                if operation == "hotspot_open":
                    assert not self.open_index and index not in self.viewed
                    if not self.viewed_on_close:
                        self.viewed.add(index)
                    self.open_index = index
                else:
                    assert self.open_index == index
                    if self.viewed_on_close:
                        self.viewed.add(index)
                    self.open_index = 0
        snapshot = await super().evaluate(expression)
        snapshot.update({
            "hotspot_count": len(self.LABELS),
            "hotspot_markers": [
                {"id": f"bubble-{index}", "label": label,
                 "content": content, "viewed": index in self.viewed,
                 "expanded": index == self.open_index}
                for index, (label, content) in enumerate(
                    zip(self.current_labels(), self.CONTENTS), start=1)
            ],
            "hotspot_signature": self.current_signature(),
            "hotspot_viewed_count": len(self.viewed),
            "hotspot_open_index": self.open_index,
        })
        return snapshot


class ResponsibleHotspotInnerTab(HotspotInnerTab):
    NAMES = ["Fairness", "Reliability & Safety", "Privacy & Security",
             "Inclusiveness", "Transparency & explainability", "Accountability",
             "Gender", "Religion", "Medical history", "Race", "National origin",
             "Marital status", "Age", "Disability"]
    LABELS = [f"Marker, {name}, Plus" for name in NAMES]
    CONTENTS = [
        f"{name} describes a responsible AI principle in enough detail " * 3
        if index < 6 else name
        for index, name in enumerate(NAMES)
    ]

    def __init__(self):
        super().__init__()
        self.viewed = set(range(1, 7))


class Runner:
    def __init__(self, outer=None):
        self.course = {"url": COURSE}
        self.tab = outer or OuterTab()
        self.events = []

    async def verified_item(self, item, wait=0):
        assert item is ITEM
        return self.tab.marked and not self.tab.no_green

    def event(self, name, **fields):
        self.events.append((name, fields))


class RiseFlowTests(unittest.IsolatedAsyncioTestCase):
    async def run_flow(self, inner, runner=None, **kwargs):
        runner = runner or Runner()
        with patch.object(flow, "open_scorm_frame", return_value=inner) as opened:
            await flow.complete_rise_scorm(
                runner, ITEM, settle_delay=0, terminal_delay=.001, **kwargs
            )
            opened.assert_awaited_once_with(runner.tab, FRAME)
        return runner

    async def test_static_accordions_read_before_mark(self):
        inner = InnerTab([{"accordions": 3, "cards": 0, "max": 900, "continue": False}])
        runner = await self.run_flow(inner)
        self.assertEqual(inner.calls.count("expand"), 3)
        self.assertEqual(inner.calls.count("scroll"), 2)
        self.assertEqual(inner.calls.count("continue"), 0)
        self.assertEqual(runner.tab.calls, ["inspect", "inspect", "mark"])
        self.assertTrue(runner.tab.marked)
        self.assertTrue(inner.closed)

    async def test_continue_and_flashcards_read_before_mark(self):
        inner = InnerTab([
            {"accordions": 2, "cards": 0, "max": 500, "continue": True},
            {"accordions": 0, "cards": 3, "max": 700, "continue": False},
        ])
        runner = await self.run_flow(inner)
        self.assertEqual(inner.calls.count("continue"), 1)
        self.assertEqual(inner.calls.count("flip"), 3)
        self.assertEqual(runner.events[0][0], "rise_scorm_mark_clicked")
        self.assertTrue(runner.tab.marked)

    async def test_quote_carousel_visits_all_five_slides_before_mark(self):
        class VisitGuard(OuterTab):
            def __init__(self, inner):
                super().__init__()
                self.inner = inner

            async def evaluate(self, expression):
                if action(expression) == "mark":
                    assert self.inner.carousel_visited == {1, 2, 3, 4, 5}
                return await super().evaluate(expression)

        for start in (1, 5):
            with self.subTest(start=start):
                inner = CarouselInnerTab(active=start)
                runner = Runner(VisitGuard(inner))
                await self.run_flow(inner, runner)
                self.assertEqual(inner.carousel_visited, {1, 2, 3, 4, 5})
                self.assertEqual(
                    inner.carousel_clicks,
                    [index for index in range(1, 6) if index != start],
                )
                self.assertTrue(runner.tab.marked)
                self.assertTrue(inner.closed)

    async def test_quote_carousel_unchanged_slide_never_marks(self):
        inner = CarouselInnerTab(active=5, stuck=True)
        runner = Runner()
        with self.assertRaises(StopRun):
            await self.run_flow(inner, runner, max_actions=10)
        self.assertEqual(inner.carousel_visited, {5})
        self.assertFalse(runner.tab.marked)
        self.assertTrue(inner.closed)

    async def test_static_tabs_read_before_mark(self):
        for active in (1, 2):
            with self.subTest(active=active):
                inner = StaticTabsInnerTab(active=active)
                runner = await self.run_flow(inner)
                self.assertEqual(inner.visited, {1, 2})
                self.assertEqual(inner.tab_clicks, [3 - active])
                self.assertTrue(runner.tab.marked)

    async def test_static_tab_that_does_not_activate_never_marks(self):
        inner = StaticTabsInnerTab(stuck=True)
        runner = Runner()
        with self.assertRaisesRegex(StopRun, "did not advance"):
            await self.run_flow(inner, runner)
        self.assertEqual(inner.visited, {1})
        self.assertFalse(runner.tab.marked)

    async def test_labeled_graphic_open_read_close_all_before_mark(self):
        class VisitGuard(OuterTab):
            def __init__(self, inner):
                super().__init__()
                self.inner = inner

            async def evaluate(self, expression):
                if action(expression) == "mark":
                    assert self.inner.viewed == {1, 2, 3}
                    assert self.inner.open_index == 0
                return await super().evaluate(expression)

        for already_viewed in (False, True):
            with self.subTest(already_viewed=already_viewed):
                inner = HotspotInnerTab(viewed=already_viewed)
                runner = Runner(VisitGuard(inner))
                await self.run_flow(inner, runner)
                self.assertEqual(inner.viewed, {1, 2, 3})
                self.assertEqual(inner.open_index, 0)
                self.assertEqual(inner.calls.count("expand"), 4)
                self.assertEqual(
                    inner.hotspot_clicks,
                    [] if already_viewed else [
                        (step, index) for index in (1, 2, 3)
                        for step in ("hotspot_open", "hotspot_close")
                    ],
                )
                self.assertTrue(runner.tab.marked)
                self.assertTrue(inner.closed)

    async def test_labeled_graphic_partial_open_or_close_never_marks(self):
        for stuck in ("hotspot_open", "hotspot_close"):
            with self.subTest(stuck=stuck):
                inner = HotspotInnerTab(stuck=stuck)
                runner = Runner()
                with self.assertRaises(StopRun):
                    await self.run_flow(inner, runner, max_actions=12)
                self.assertFalse(runner.tab.marked)
                self.assertTrue(inner.closed)

    async def test_plus_markers_with_minus_open_state_read_before_mark(self):
        inner = HotspotInnerTab(plus=True)
        runner = await self.run_flow(inner)
        self.assertEqual(inner.viewed, {1, 2, 3})
        self.assertEqual(inner.open_index, 0)
        self.assertEqual(len(inner.hotspot_clicks), 6)
        self.assertTrue(runner.tab.marked)

    async def test_marker_becomes_viewed_only_after_dialog_closes(self):
        inner = HotspotInnerTab(plus=True, viewed_on_close=True)
        runner = await self.run_flow(inner)
        self.assertEqual(inner.viewed, {1, 2, 3})
        self.assertEqual(inner.hotspot_clicks, [
            (step, index) for index in range(1, 4)
            for step in ("hotspot_open", "hotspot_close")
        ])
        self.assertTrue(runner.tab.marked)

    async def test_two_responsible_ai_graphics_read_all_fourteen_markers(self):
        class ResponsibleOuterTab(OuterTab):
            async def evaluate(self, expression):
                result = await super().evaluate(expression)
                result["frame_title"] = "Responsible AI"
                return result

        inner = ResponsibleHotspotInnerTab()
        runner = Runner(ResponsibleOuterTab())
        with patch.object(flow, "lesson_heading", return_value="Responsible AI"):
            await self.run_flow(inner, runner, max_actions=50)
        self.assertEqual(inner.viewed, set(range(1, 15)))
        self.assertEqual(inner.open_index, 0)
        self.assertEqual(inner.hotspot_clicks, [
            (step, index) for index in range(7, 15)
            for step in ("hotspot_open", "hotspot_close")
        ])
        self.assertTrue(runner.tab.marked)

    async def test_exclusive_accordions_survive_dom_rerender(self):
        inner = InnerTab(
            [{"accordions": 5, "cards": 0, "max": 600, "continue": False}],
            exclusive=True, rerender_ids=True,
        )
        runner = await self.run_flow(inner)
        self.assertEqual(inner.calls.count("expand"), 5)
        self.assertEqual(len(inner.expanded), 1)
        self.assertTrue(runner.tab.marked)

    async def test_changed_collapsed_panel_is_read_again(self):
        inner = InnerTab(
            [{"accordions": 2, "cards": 0, "max": 0, "continue": False}],
            exclusive=True, changed_first_panel=True,
        )
        runner = await self.run_flow(inner)
        self.assertEqual(inner.calls.count("expand"), 3)
        self.assertTrue(runner.tab.marked)

    async def test_transition_gap_is_not_mistaken_for_terminal(self):
        inner = InnerTab([
            {"accordions": 0, "cards": 0, "max": 0, "continue": True},
            {"accordions": 0, "cards": 0, "max": 0, "continue": False},
            {"accordions": 2, "cards": 0, "max": 500, "continue": False},
        ], transition_gap=True)
        runner = await self.run_flow(inner)
        self.assertEqual(inner.stage, 2)
        self.assertEqual(inner.calls.count("expand"), 2)
        self.assertTrue(runner.tab.marked)

    async def test_initial_rise_route_pending_retries_then_reads_and_marks(self):
        class MountingTab(InnerTab):
            pending_inspects = 3

            async def evaluate(self, expression):
                if action(expression) == "inspect" and self.pending_inspects:
                    self.pending_inspects -= 1
                    self.calls.append("inspect")
                    return {"pending": "Rise content route is mounting."}
                return await super().evaluate(expression)

        inner = MountingTab([
            {"accordions": 0, "cards": 0, "max": 500, "continue": False}
        ])
        runner = await self.run_flow(inner)
        self.assertEqual(inner.calls[:4], ["inspect"] * 4)
        self.assertTrue(runner.tab.marked)

    async def test_initial_rise_route_pending_is_bounded(self):
        class NeverMountedTab(InnerTab):
            async def evaluate(self, expression):
                self.calls.append(action(expression))
                return {"pending": "Rise content route is mounting."}

        inner = NeverMountedTab([])
        runner = Runner()
        with self.assertRaisesRegex(StopRun, "probe limit"):
            await self.run_flow(inner, runner)
        self.assertEqual(inner.calls, ["inspect"] * flow.SETTLE_PROBES)
        self.assertFalse(runner.tab.marked)
        self.assertTrue(inner.closed)

    async def test_exact_lesson_index_transition_gets_bounded_extra_probes(self):
        class TransitionTab(InnerTab):
            def __init__(self, pending_count, *, exact_route=True):
                super().__init__([{
                    "accordions": 0, "cards": 0, "max": 0, "continue": False,
                }])
                self.pending_count = pending_count
                self.exact_route = exact_route

            async def evaluate(self, expression):
                if action(expression) == "inspect" and self.pending_count:
                    self.pending_count -= 1
                    self.calls.append("inspect")
                    return {"pending": "Rise lesson route is initializing.",
                            "diagnostic": {
                                "route_class": "content-index-transition",
                                "lesson_index_route": self.exact_route,
                                "suffix_kind": "hash", "suffix_length": 9,
                                "has_hash": True,
                            }}
                return await super().evaluate(expression)

        request = {"frame": FRAME, "frame_title": "Defence techniques and strategies"}
        with patch.object(flow, "LESSON_INDEX_PROBES", 6), \
                patch.object(flow, "INITIAL_MOUNT_PROBES", 6), \
                patch.object(flow.asyncio, "sleep", new_callable=AsyncMock):
            eventually_ready = TransitionTab(4)
            result = await flow._inner(eventually_ready, request, "inspect",
                                       probes=3, delay=0)
            self.assertEqual(result["lesson"], LESSON)
            self.assertEqual(eventually_ready.calls, ["inspect"] * 5)

            never_ready = TransitionTab(10)
            with self.assertRaisesRegex(StopRun, "probe limit"):
                await flow._inner(never_ready, request, "inspect", probes=3, delay=0)
            self.assertEqual(never_ready.calls, ["inspect"] * 6)

            other_hash = TransitionTab(10, exact_route=False)
            with self.assertRaisesRegex(StopRun, "probe limit"):
                await flow._inner(other_hash, request, "inspect", probes=3, delay=0)
            self.assertEqual(other_hash.calls, ["inspect"] * 3)

    async def test_verified_initial_mount_gets_bounded_extra_probes(self):
        class MountingTab(InnerTab):
            def __init__(self, pending_count, *, stage="lesson-frame"):
                super().__init__([{
                    "accordions": 0, "cards": 0, "max": 0, "continue": False,
                }])
                self.pending_count = pending_count
                self.mount_stage = stage

            async def evaluate(self, expression):
                if action(expression) == "inspect" and self.pending_count:
                    self.pending_count -= 1
                    self.calls.append("inspect")
                    return {"pending": "Rise lesson is mounting.",
                            "diagnostic": {"mount_stage": self.mount_stage}}
                return await super().evaluate(expression)

        request = {"frame": FRAME, "frame_title": "Defence techniques and strategies"}
        with patch.object(flow, "INITIAL_MOUNT_PROBES", 6), \
                patch.object(flow, "LESSON_INDEX_PROBES", 6), \
                patch.object(flow.asyncio, "sleep", new_callable=AsyncMock):
            eventually_ready = MountingTab(4)
            result = await flow._inner(eventually_ready, request, "inspect",
                                       probes=3, delay=0)
            self.assertEqual(result["lesson"], LESSON)
            self.assertEqual(eventually_ready.calls, ["inspect"] * 5)

            never_ready = MountingTab(10)
            with self.assertRaisesRegex(
                    StopRun, "probe limit.*Last pending: Rise lesson is mounting"):
                await flow._inner(never_ready, request, "inspect", probes=3, delay=0)
            self.assertEqual(never_ready.calls, ["inspect"] * 6)

            mismatched_stage = MountingTab(10, stage="driver")
            with self.assertRaisesRegex(StopRun, "probe limit"):
                await flow._inner(mismatched_stage, request, "inspect", probes=3, delay=0)
            self.assertEqual(mismatched_stage.calls, ["inspect"] * 3)

            established = MountingTab(10)
            with self.assertRaisesRegex(StopRun, "probe limit"):
                await flow._inner(established, {**request, "lesson": LESSON},
                                  "inspect", probes=3, delay=0)
            self.assertEqual(established.calls, ["inspect"] * 3)

    async def test_unknown_activity_stops_without_mark(self):
        inner = InnerTab([], error="Rise lesson has an unrecognized button or activity.")
        runner = Runner()
        with self.assertRaisesRegex(StopRun, "unrecognized button"):
            await self.run_flow(inner, runner)
        self.assertFalse(runner.tab.marked)
        self.assertTrue(inner.closed)

    async def test_unchanged_continue_stops_without_mark(self):
        inner = InnerTab(
            [{"accordions": 0, "cards": 0, "max": 0, "continue": True}],
            stuck_continue=True,
        )
        runner = Runner()
        with self.assertRaisesRegex(StopRun, "did not reveal"):
            await self.run_flow(inner, runner)
        self.assertFalse(runner.tab.marked)

    async def test_no_green_after_mark_stops(self):
        inner = InnerTab([{"accordions": 0, "cards": 0, "max": 0, "continue": False}])
        runner = Runner(OuterTab(no_green=True))
        with self.assertRaisesRegex(StopRun, "green completion mark"):
            await self.run_flow(inner, runner)
        self.assertTrue(runner.tab.marked)

    async def test_wrong_outer_asset_never_opens_frame(self):
        runner = Runner(OuterTab(wrong_frame=True))
        with patch.object(flow, "open_scorm_frame") as opened:
            with self.assertRaisesRegex(StopRun, "recognized static Rise widget"):
                await flow.complete_rise_scorm(runner, ITEM)
            opened.assert_not_called()
        self.assertFalse(runner.tab.marked)

    async def test_wrong_item_identity_never_opens_frame(self):
        invalid = {**ITEM, "key": "ungradedWidget:wrong"}
        runner = Runner()
        with patch.object(flow, "open_scorm_frame") as opened:
            with self.assertRaises(StopRun):
                await flow.complete_rise_scorm(runner, invalid)
            opened.assert_not_called()

    async def test_unlisted_widget_routes_to_rise_only_in_exact_course(self):
        item = {
            "key": "ungradedWidget:Zz001",
            "title": "Another static lesson Ungraded Plugin • 10 min",
            "url": COURSE + "/ungradedWidget/Zz001/another-static-lesson",
        }
        runner = Runner()
        with patch.object(flow, "complete_rise_scorm", new_callable=AsyncMock) as reader:
            await plugins.complete_plugin(runner, item)
            reader.assert_awaited_once_with(runner, item)
        self.assertEqual(runner.tab.calls, [])

    async def test_audited_widget_keeps_exact_handler_priority(self):
        runner = Runner()
        with (
            patch.object(audited, "complete_static_scorm", new_callable=AsyncMock) as exact,
            patch.object(flow, "complete_rise_scorm", new_callable=AsyncMock) as generic,
        ):
            await plugins.complete_plugin(runner, ITEM)
            exact.assert_awaited_once_with(
                runner, ITEM,
                "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
                "/ungradedWidget/gTEoB/defence-techniques-and-strategies",
                "Defence techniques and strategies",
            )
            generic.assert_not_awaited()
        self.assertEqual(runner.tab.calls, [])

    async def test_audited_widget_heading_change_does_not_fall_back(self):
        item = {**ITEM, "title": "Changed topic Ungraded Plugin • 30 min"}
        runner = Runner()
        with patch.object(flow, "complete_rise_scorm", new_callable=AsyncMock) as generic:
            with self.assertRaisesRegex(StopRun, "changed its course-outline heading"):
                await plugins.complete_plugin(runner, item)
            generic.assert_not_awaited()
        self.assertEqual(runner.tab.calls, [])

    async def test_other_course_and_plugin_type_never_route_to_rise(self):
        class ActivityTab:
            async def evaluate(self, expression):
                return {
                    "ready": "complete", "mark_count": 1, "mark_disabled": False,
                    "activity_count": 1, "modal_count": 0, "other_actions": [],
                }

        cases = [
            (
                "https://www.coursera.org/learn/another-course",
                {
                    "key": "ungradedWidget:Zz001",
                    "title": "Activity Ungraded Widget • 10 min",
                    "url": "https://www.coursera.org/learn/another-course"
                           "/ungradedWidget/Zz001/activity",
                },
            ),
            (
                COURSE,
                {
                    "key": "ungradedPlugin:Zz001",
                    "title": "Activity Ungraded Plugin • 10 min",
                    "url": COURSE + "/ungradedPlugin/Zz001/activity",
                },
            ),
        ]
        for course, item in cases:
            with self.subTest(course=course, key=item["key"]):
                runner = Runner()
                runner.course = {"url": course}
                runner.tab = ActivityTab()
                runner.verified_item = AsyncMock(return_value=False)
                with patch.object(flow, "complete_rise_scorm", new_callable=AsyncMock) as generic:
                    with self.assertRaisesRegex(StopRun, "embedded activity"):
                        await plugins.complete_plugin(runner, item)
                    generic.assert_not_awaited()

    async def test_rise_failure_propagates_without_simple_mark(self):
        item = {
            "key": "ungradedWidget:Zz001",
            "title": "Unknown scenario Ungraded Widget • 10 min",
            "url": COURSE + "/ungradedWidget/Zz001/unknown-scenario",
        }
        runner = Runner()
        with patch.object(
            flow, "complete_rise_scorm", new_callable=AsyncMock,
            side_effect=StopRun("Rise lesson has an unrecognized button or activity."),
        ) as generic:
            with self.assertRaisesRegex(StopRun, "unrecognized button"):
                await plugins.complete_plugin(runner, item)
            generic.assert_awaited_once_with(runner, item)
        self.assertEqual(runner.tab.calls, [])

    def test_browser_scripts_compile_and_reject_wrong_origin(self):
        script = """
const fs = require('node:fs');
const {source, inner} = JSON.parse(fs.readFileSync(0, 'utf8'));
const outer = new Function('return ' + source)();
const reader = new Function('return ' + inner)();
global.location = {href:'https://example.invalid/'};
global.document = {readyState:'complete'};
const a = outer({action:'inspect',path:'/learn/a/ungradedWidget/b/c',heading:'c'});
const b = reader({action:'inspect',frame:'https://example.invalid/index.html'});
if (!a.error || !b.error) process.exit(2);
"""
        result = subprocess.run(
            ["node", "-e", script],
            input=json.dumps({"source": flow.OUTER_JS, "inner": flow.INNER_JS}),
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_inner_rise_initial_route_pending_but_changed_identity_fails(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
const unknownButton={getClientRects:()=>[{}],matches:()=>false};
const page={URL:data.innerUrl,readyState:'complete',
 querySelector:()=>null,
 querySelectorAll:key=>key==='iframe'?(data.nestedFrame?[{}]:[]):
   key==='button,[role="button"]'&&data.unknownButton?[unknownButton]:[]};
const content={title:'Selecting appropriate controls',contentDocument:page};
const auxiliary=[
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}];
const driver={readyState:'complete',querySelector:()=>null,querySelectorAll:key=>
 key==='iframe#content-frame'?[content]:key==='iframe'?[content,...auxiliary]:[]};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',
 contentDocument:driver};
const document={readyState:'complete',querySelector:()=>null,querySelectorAll:key=>
 key==='iframe[title="container"]'||key==='iframe'?[container]:[]};
const context={document,location:{href:data.frame}};
const request={frame:data.frame,frame_title:'Selecting appropriate controls',
 action:'inspect'};
if(data.expectedLesson)request.lesson=data.expectedLesson;
const result=vm.runInNewContext(data.script+'('+JSON.stringify(request)+')',context);
process.stdout.write(JSON.stringify(result));
'''
        base = FRAME.replace("index.html", "")
        initial_urls = (
            "about:blank",
            base + "scorm-content/scormdriver/blank.html",
            base + "scorm-content/scormdriver/indexAPI.html",
            base + "scorm-content/scormcontent/index.html",
            base + "scorm-content/scormcontent/index.html#",
            base + "scorm-content/scormcontent/index.html#/",
            base + "scorm-content/scormcontent/index.html#/lessons",
            base + "scorm-content/scormcontent/index.html#/lessons/",
            base + "scorm-content/scormcontent/index.html#/router-mounting",
            base + "scorm-content/scormcontent/index.html?mount=1",
            base + "scorm-content/scormcontent/index.html?mount=1#/loading",
            base + "scorm-content/scormcontent/index.html#/quiz/start",
        )
        unknown_urls = (
            base + "scorm-content/scormcontent/other.html",
            base + "scorm-content/scormcontent/index.html.bad#/loading",
            "https://example.invalid/index.html#/lessons/" + LESSON,
        )
        cases = (
            *((url, None, False, False, "pending") for url in initial_urls),
            *((url, None, False, False, "error") for url in unknown_urls),
            (base + "scorm-content/scormcontent/index.html#/",
             LESSON, False, False, "error"),
            (LESSON_URL.replace(LESSON, "another-valid-lesson-identifier"),
             LESSON, False, False, "error"),
            (LESSON_URL, LESSON, True, False, "error"),
            (base + "scorm-content/scormcontent/index.html#/router-mounting",
             None, False, True, "error"),
        )
        for inner_url, expected_lesson, nested_frame, unknown_button, outcome in cases:
            with self.subTest(inner_url=inner_url, expected=expected_lesson):
                result = subprocess.run(
                    ["node", "-e", harness],
                    input=json.dumps({
                        "script": flow.INNER_JS, "frame": FRAME,
                        "innerUrl": inner_url, "expectedLesson": expected_lesson,
                        "nestedFrame": nested_frame,
                        "unknownButton": unknown_button,
                    }),
                    capture_output=True, text=True, check=True, timeout=10,
                )
                snapshot = json.loads(result.stdout)
                self.assertIn(outcome, snapshot, snapshot)

    def test_valid_lesson_route_waits_for_initial_reading_scaffold_only(self):
        frame = (
            "https://d10o6em2qtnr4q.cloudfront.net/assets/"
            "66943ffd39414670aafd32e605e99107/index.html"
        )
        lesson = "kt93j64Tn6bTo_xisFPF_TCntDMg_Dp-"
        lesson_url = frame.replace("index.html", "") + (
            "scorm-content/scormcontent/index.html#/lessons/" + lesson
        )
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
const state={kind:'valid'};
const longText=('Selecting appropriate controls. This lesson compares static ' +
  'AI security controls and explains when to use each defence. ').repeat(6);
const input={matches:selector=>selector==='input[type="search"][disabled]'};
const form={matches:selector=>selector==='form.nav-sidebar-search--disabled',
  querySelectorAll:selector=>selector==='input'?[input]:[]};
const unknownButton={getClientRects:()=>[{}],matches:()=>false};
const wrap={scrollHeight:950,clientHeight:550,scrollTop:0,
  get innerText(){return state.kind==='short_text'?'Short preview.':longText;},
  querySelectorAll:selector=>selector==='a[href]'?[]:[]};
const progressNode={get innerText(){return state.kind==='invalid_progress'?'Loading':
  '0% COMPLETE';}};
const page={get URL(){return state.kind==='route_lesson_index'?
  data.indexUrl:state.kind.startsWith('route_')?
  data.unseenUrl:data.lessonUrl;},readyState:'complete',
  body:{get innerText(){return state.kind==='missing_label'?longText:
    state.kind==='wrong_label'?'LESSON 2 OF 2 '+longText:
    'LESSON 1 OF 1 '+longText;}},
  querySelectorAll:key=>{
    if(key==='iframe')return [];
    if(key==='div.page-wrap')return state.kind==='no_wrap'?[]:[wrap];
    if(key==='.nav-sidebar-header__progress-text')
      return state.kind==='missing_progress'?[]:[progressNode];
    if(key==='form')return [form];
    if(key==='input')return [input];
    if(key==='button,[role="button"]')
      return ['unknown_button','route_unknown_button'].includes(state.kind)?
        [unknownButton]:[];
    return [];
  },
  querySelector:key=>{
    if(key==='.nav-sidebar-header__progress-text')
      return state.kind==='missing_progress'?null:progressNode;
    if(state.kind==='activity' && key.startsWith('textarea,select,'))return {};
    return null;
  }};
const content={title:'Selecting appropriate controls',contentDocument:page};
const auxiliary=[
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}];
const driver={readyState:'complete',querySelector:()=>null,
  querySelectorAll:key=>key==='iframe#content-frame'?[content]:
    key==='iframe'?[content,...auxiliary]:[]};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',
  contentDocument:driver};
const document={readyState:'complete',querySelector:()=>null,
  querySelectorAll:key=>key==='iframe[title="container"]'||key==='iframe'?
    [container]:[]};
const context={document,location:{href:data.frame},URL};
function inspect(kind,established=false){
  state.kind=kind;
  const request={frame:data.frame,frame_title:'Selecting appropriate controls',
    action:'inspect'};
  if(established)request.lesson=data.lesson;
  return vm.runInNewContext(data.script+'('+JSON.stringify(request)+')',context);
}
const incomplete=['no_wrap','short_text','missing_label','missing_progress'];
const results={initial:{},established:{},unsafe:{}};
for(const kind of incomplete){
  results.initial[kind]=inspect(kind);
  results.established[kind]=inspect(kind,true);
}
for(const kind of ['wrong_label','invalid_progress','unknown_button','activity'])
  results.unsafe[kind]=inspect(kind);
results.routePending=inspect('route_transition');
results.lessonIndexPending=inspect('route_lesson_index');
results.routeBlocked=inspect('route_unknown_button');
results.routeConverged=inspect('valid');
results.converged=inspect('valid');
process.stdout.write(JSON.stringify(results));
'''
        result = subprocess.run(
            ["node", "-e", harness],
            input=json.dumps({
                "script": flow.INNER_JS, "frame": frame,
                "lessonUrl": lesson_url, "lesson": lesson,
                "unseenUrl": frame.replace("index.html", "") +
                             "scorm-content/scormcontent/index.html#/mounting-topic",
                "indexUrl": frame.replace("index.html", "") +
                            "scorm-content/scormcontent/index.html#/lessons",
            }),
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshots = json.loads(result.stdout)
        for kind in ("no_wrap", "short_text", "missing_label",
                     "missing_progress"):
            with self.subTest(kind=kind):
                initial = snapshots["initial"][kind]
                self.assertIn("pending", initial, initial)
                self.assertNotIn("error", initial, initial)
                self.assertEqual(set(initial["diagnostic"]), {
                    "wrap_count", "text_length", "body_length", "lesson_marker",
                    "progress_nodes", "progress_valid", "mount_stage",
                })
                self.assertEqual(initial["diagnostic"]["mount_stage"],
                                 "lesson-scaffold")
                established = snapshots["established"][kind]
                self.assertIn("error", established, established)
                self.assertNotIn("pending", established, established)
        for kind, snapshot in snapshots["unsafe"].items():
            with self.subTest(kind=kind):
                self.assertIn("error", snapshot, snapshot)
                self.assertNotIn("pending", snapshot, snapshot)
        self.assertIn("pending", snapshots["routePending"])
        self.assertEqual(
            snapshots["routePending"]["diagnostic"]["suffix_kind"], "hash"
        )
        self.assertFalse(
            snapshots["routePending"]["diagnostic"]["lesson_index_route"]
        )
        self.assertNotIn("mount_stage", snapshots["routePending"]["diagnostic"])
        self.assertIn("pending", snapshots["lessonIndexPending"])
        self.assertTrue(
            snapshots["lessonIndexPending"]["diagnostic"]["lesson_index_route"]
        )
        self.assertEqual(
            snapshots["lessonIndexPending"]["diagnostic"]["mount_stage"],
            "lesson-route",
        )
        self.assertIn("error", snapshots["routeBlocked"])
        self.assertNotIn("pending", snapshots["routeBlocked"])
        self.assertEqual(snapshots["routeConverged"]["lesson"], lesson)
        converged = snapshots["converged"]
        self.assertNotIn("pending", converged, converged)
        self.assertNotIn("error", converged, converged)
        self.assertEqual(converged["lesson_url"], lesson_url)
        self.assertEqual(converged["lesson"], lesson)
        self.assertGreaterEqual(converged["text_length"], 200)

    def test_labeled_graphic_dom_and_controls_fail_closed(self):
        frame = (
            "https://d10o6em2qtnr4q.cloudfront.net/assets/"
            "76e120914f114d4c96599da322f6dc1b/index.html"
        )
        lesson = "4eEnNR4c1YwGXd8x9yJY6Y5On95AtO6k"
        lesson_url = frame.replace("index.html", "") + (
            "scorm-content/scormcontent/index.html#/lessons/" + lesson
        )
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
const state={viewed:new Set(),open:0,invalid:null};
const names=['Blue team (defensive AI)','Red team (offensive AI)','Purple team'];
const arrows=['Right','Left','Up'];
const content=names.map((name,index)=>name+' explains team responsibilities and '+
 'the testing and benchmarking controls required to protect AI systems. '.repeat(index+3));
const visible=()=>[{}];
const controls=(index)=>[
 {type:'button',getAttribute:key=>key==='aria-label'?
  (state.invalid==='wrong_close'&&index===0?'Dismiss':'Close modal'):null,
  getClientRects:()=>state.open===index+1?visible():[],
  scrollIntoView:()=>{},click:()=>{state.open=0}},
 {type:'button',getAttribute:key=>key==='aria-label'?'Previous':null,
  getClientRects:()=>state.open===index+1?visible():[]},
 {type:'button',getAttribute:key=>key==='aria-label'?'Next':null,
  getClientRects:()=>state.open===index+1?visible():[]}
];
const dialogs=names.map((_,index)=>{
 const buttons=controls(index);
 return {id:'bubble-'+(index+1),tagName:'DIALOG',
  textContent:content[index],
  classList:{contains:key=>key==='bubble__body'},
  closest:key=>key==='#portal'?{}:null,
  getAttribute:key=>key==='aria-hidden'?(state.open===index+1?'false':'true'):null,
  hasAttribute:key=>key==='open'&&state.open===index+1,
  getClientRects:()=>state.open===index+1&&state.invalid!=='hidden_open'?visible():[],
  querySelector:key=>key==='button.bubble__close[aria-label="Close modal"]'?
   buttons[0]:state.invalid==='dialog_activity'&&index===0?{}:null,
  querySelectorAll:key=>key==='button.bubble__close'?[buttons[0]]:
   key==='button.bubble__prev'?[buttons[1]]:
   key==='button.bubble__next'?[buttons[2]]:
   key==='button'?buttons:key==='a[href]'&&
   state.invalid==='dialog_action_link'&&index===0?
   [{innerText:'Launch lab',textContent:'Launch lab',className:'',
    getClientRects:()=>[],getAttribute:key=>key==='href'?
     'https://example.org/lab':null,hasAttribute:key=>key==='href'}]:[]};
});
const markers=names.map((name,index)=>({
 type:'button',
 getAttribute:key=>key==='aria-label'?
  (state.invalid==='bad_label'&&index===0?'Unrecognized marker':
   `Marker, ${name}, ${arrows[index]} arrow, `+
   (state.viewed.has(index+1)?'Viewed':'Not viewed')):
  key==='aria-controls'?
   (state.invalid==='bad_control'&&index===0?'other-id':'bubble-'+(index+1)):
  key==='aria-expanded'? (state.open===index+1?'true':'false'):null,
 closest:key=>key==='li.map-item[role="listitem"]'?{}:null,
 getClientRects:visible,scrollIntoView:()=>{},
 click:()=>{state.viewed.add(index+1);state.open=index+1}
}));
const group={
 querySelectorAll:key=>key==='button.labeled-graphic-marker'?markers:
   [],
 querySelector:key=>key==='.labeled-graphic-canvas__figure > ol[role="list"]'?{}:null,
 contains:node=>dialogs.includes(node)
};
const wrap={innerText:('AI security testing and benchmarking static reading. ').repeat(15),
 scrollHeight:950,clientHeight:550,scrollTop:0,
 contains:node=>node===group,querySelectorAll:()=>[]};
const input={matches:key=>key==='input[type="search"][disabled]'};
const form={matches:key=>key==='form.nav-sidebar-search--disabled',
 querySelectorAll:key=>key==='input'?[input]:[]};
const progress={innerText:'0% COMPLETE'};
const extra={getClientRects:visible};
const page={URL:data.lessonUrl,readyState:'complete',
 body:{innerText:'AI security testing and benchmarking LESSON 1 OF 1'},
 getElementById:id=>dialogs.find(dialog=>dialog.id===id)||null,
 querySelector:key=>key==='.nav-sidebar-header__progress-text'?progress:
  key==='div.page-wrap'?wrap:null,
 querySelectorAll:key=>key==='iframe'?[]:
  key==='div.page-wrap'?[wrap]:
  key==='.nav-sidebar-header__progress-text'?[progress]:
  key==='form'?[form]:key==='input'?[input]:
  key==='div.view-labeled-graphic[role="group"][aria-label="Labeled graphic"]'?[group]:
  key==='button.labeled-graphic-marker'?markers:
  key==='dialog.bubble__body'?dialogs:
  key==='#portal dialog.bubble__body'?dialogs:
  key==='button,[role="button"]'?
    (state.invalid==='extra_button'?[...markers,...dialogs.flatMap((_,i)=>
     dialogs[i].querySelectorAll('button')),extra]:
     [...markers,...dialogs.flatMap((_,i)=>dialogs[i].querySelectorAll('button'))]):[]};
const contentFrame={title:'AI security testing and benchmarking',contentDocument:page};
const auxiliary=[
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}];
const driver={readyState:'complete',querySelector:()=>null,
 querySelectorAll:key=>key==='iframe#content-frame'?[contentFrame]:
  key==='iframe'?[contentFrame,...auxiliary]:[]};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',
 contentDocument:driver};
const document={readyState:'complete',querySelector:()=>null,
 querySelectorAll:key=>key==='iframe[title="container"]'||key==='iframe'?
  [container]:[]};
const context={document,location:{href:data.frame},URL};
function run(action,extra={}){
 const request={frame:data.frame,frame_title:'AI security testing and benchmarking',
  action,...extra};
 return vm.runInNewContext(data.script+'('+JSON.stringify(request)+')',context);
}
const results={initial:run('inspect'),opened:[],closed:[],negative:{}};
let current=results.initial;
for(let index=1;index<=3;index++){
 const open=run('hotspot_open',{hotspot_index:index,
  hotspot_signature:current.hotspot_signature,stage_signature:current.signature});
 results.opened.push(open);
 current=run('inspect');
 const close=run('hotspot_close',{hotspot_index:index,
  hotspot_signature:current.hotspot_signature,stage_signature:current.signature});
 results.closed.push(close);
 current=run('inspect');
}
results.completed=current;
results.resume=run('inspect');
state.viewed=new Set();state.open=0;
const partial=run('inspect');
results.prematureScroll=run('scroll',{stage_signature:partial.signature,
 panels_read:true,carousel_read:true,hotspots_read:false,
 accordion_signature:partial.accordion_signature});
results.prematureContinue=run('continue',{stage_signature:partial.signature,
 panels_read:true,carousel_read:true,hotspots_read:false,
 accordion_signature:partial.accordion_signature});
results.wrongSignature=run('hotspot_open',{hotspot_index:1,
 hotspot_signature:'changed',stage_signature:partial.signature});
for(const kind of ['bad_label','bad_control','wrong_close','dialog_activity',
 'dialog_action_link','extra_button']){
 state.invalid=kind;results.negative[kind]=run('inspect');state.invalid=null;
}
state.viewed=new Set([1]);state.open=1;
results.hiddenOpen=(()=>{state.invalid='hidden_open';const result=run('inspect');
 state.invalid=null;return result})();
results.resumeOpen=run('inspect');
process.stdout.write(JSON.stringify(results));
'''
        result = subprocess.run(
            ["node", "-e", harness],
            input=json.dumps({
                "script": flow.INNER_JS, "frame": frame,
                "lessonUrl": lesson_url,
            }),
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshots = json.loads(result.stdout)
        self.assertEqual(snapshots["initial"]["hotspot_count"], 3)
        self.assertEqual(snapshots["initial"]["hotspot_viewed_count"], 0)
        self.assertEqual([entry.get("clicked") for entry in snapshots["opened"]],
                         ["hotspot_open"] * 3)
        self.assertEqual([entry.get("clicked") for entry in snapshots["closed"]],
                         ["hotspot_close"] * 3)
        self.assertEqual(snapshots["completed"]["hotspot_viewed_count"], 3)
        self.assertEqual(snapshots["completed"]["hotspot_open_index"], 0)
        self.assertEqual(snapshots["resume"]["hotspot_viewed_count"], 3)
        self.assertEqual(snapshots["resumeOpen"]["hotspot_open_index"], 1)
        for name in ("prematureScroll", "prematureContinue", "wrongSignature",
                     "hiddenOpen"):
            self.assertIn("error", snapshots[name], name)
        for kind, snapshot in snapshots["negative"].items():
            with self.subTest(kind=kind):
                self.assertIn("error", snapshot, snapshot)

    def test_quote_carousel_dom_and_controls_fail_closed(self):
        frame = (
            "https://d10o6em2qtnr4q.cloudfront.net/assets/"
            "1a0bba8100f1478ba74ae747427cdc16/index.html"
        )
        lesson = "wOh5qlt2pRSnvMC565bGIi4no8IHKrYQ"
        lesson_url = frame.replace("index.html", "") + (
            "scorm-content/scormcontent/index.html#/lessons/" + lesson
        )
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
const state={active:1,invalid:null};
const contents=[
 'Evaluating Security Risk in DeepSeek and Other Frontier Reasoning Models. Cisco Blogs.',
 'Hacking Google Bard - From Prompt Injection to Data Exfiltration. Wunderwuzzi blog.',
 'Azure AI announces Prompt Shields for Jailbreak and Indirect prompt injection attacks.',
 'Lessons learned from ChatGPT Samsung leak. Cybernews.com.',
 'Microsoft RAG Copilot can be tricked into leaking enterprise secrets. The Stack.'
];
const hiddenLink=(href,label)=>({
 innerText:label,textContent:label,className:'',
 getClientRects:()=>[],
 getAttribute:name=>name==='href'?href:null,
 hasAttribute:name=>name==='href'
});
const slides=contents.map((content,index)=>({
 textContent:content,
 getAttribute:name=>name==='role'?'group':
   name==='aria-roledescription'?'Slide':
   name==='aria-label'?`${index+1} of 5`:null,
 hasAttribute:name=>{
   const inactive=state.active!==index+1;
   if(state.invalid==='hide_active' && index+1===state.active && name==='hidden')
     return true;
   if(state.invalid==='inert_active' && index+1===state.active && name==='inert')
     return true;
   if(state.invalid==='expose_inactive' && index===1 && name==='hidden')
     return false;
   if(state.invalid==='uninert_inactive' && index===1 && name==='inert')
     return false;
   return (name==='hidden'||name==='inert') && inactive;
 },
 querySelector:key=>state.invalid==='slide_activity'&&index===1?{}:null,
 querySelectorAll:key=>key==='a[href]'&&index===1&&
   state.invalid==='hidden_action_link'?
   [hiddenLink('https://example.org/lab','Launch lab')]:
   key==='a[href]'&&index===1&&state.invalid==='hidden_citation'?
   [hiddenLink('https://example.org/research','Research article')]:[]
}));
const dots=contents.map((_,index)=>({
 type:'button',disabled:false,
 get tabIndex(){return state.active===index+1?0:-1;},
 getAttribute:name=>name==='aria-label'?
   (state.invalid==='wrong_dot_label'&&index===2?'Go to image 3':
    `Go to slide ${index+1}`):
   name==='aria-current'?
    (state.active===index+1||state.invalid==='second_current'&&index===1?
      'true':'false'):null,
 getClientRects:()=>[{}],scrollIntoView:()=>{},click:()=>{state.active=index+1;}
}));
const prev={type:'button',getAttribute:name=>name==='aria-label'?
  (state.invalid==='wrong_prev_label'?'Previous':'Go to previous slide'):null,
  getClientRects:()=>[{}]};
const next={type:'button',getAttribute:name=>name==='aria-label'?
  'Go to next slide':null,getClientRects:()=>[{}]};
const extra={getClientRects:()=>[{}]};
const region={
 matches:selector=>selector==='div.carousel[aria-label="Quote Carousel"]',
 querySelectorAll:key=>key==='.carousel-slide'?slides:
  key==='button.carousel-controls-item-btn'?dots:
  key==='button.carousel-controls-prev'?[prev]:
  key==='button.carousel-controls-next'?[next]:[]
};
const wrap={innerText:('Guardrail protection versus guardrail failure. '+
  'AI security controls can fail under adversarial pressure. ').repeat(8),
 scrollHeight:950,clientHeight:550,scrollTop:0,
 contains:node=>node===region,querySelectorAll:()=>[]};
const input={matches:key=>key==='input[type="search"][disabled]'};
const form={matches:key=>key==='form.nav-sidebar-search--disabled',
 querySelectorAll:key=>key==='input'?[input]:[]};
const progress={innerText:'0% COMPLETE'};
const page={URL:data.lessonUrl,readyState:'complete',
 body:{innerText:'Guardrail protection versus guardrail failure LESSON 1 OF 1'},
 querySelector:key=>key==='.nav-sidebar-header__progress-text'?progress:
  key==='div.page-wrap'?wrap:null,
 querySelectorAll:key=>key==='iframe'?[]:
  key==='div.page-wrap'?[wrap]:
  key==='.nav-sidebar-header__progress-text'?[progress]:
  key==='form'?[form]:key==='input'?[input]:
  key==='[role="region"][aria-roledescription="Carousel"]'?[region]:
  key.startsWith('button.carousel-controls-item-btn,')?[...dots,prev,next]:
  key==='button,[role="button"]'?
    (state.invalid==='extra_button'?[...dots,prev,next,extra]:[...dots,prev,next]):[]};
const content={title:'Guardrail protection versus guardrail failure',contentDocument:page};
const auxiliary=[
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}];
const driver={readyState:'complete',querySelector:()=>null,
 querySelectorAll:key=>key==='iframe#content-frame'?[content]:
  key==='iframe'?[content,...auxiliary]:[]};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',
 contentDocument:driver};
const document={readyState:'complete',querySelector:()=>null,
 querySelectorAll:key=>key==='iframe[title="container"]'||key==='iframe'?
  [container]:[]};
const context={document,location:{href:data.frame},URL};
function run(action,extra={}){
 const request={frame:data.frame,frame_title:'Guardrail protection versus guardrail failure',
  action,...extra};
 return vm.runInNewContext(data.script+'('+JSON.stringify(request)+')',context);
}
const results={initial:run('inspect'),active:[],clicks:[],negative:{}};
for(let index=1;index<=5;index++){
 const current=run('inspect');results.active.push(current);
 if(index<5){
  results.clicks.push(run('carousel_dot',{
   carousel_index:index+1,stage_signature:current.signature,
   carousel_signature:current.carousel_signature}));
 }
}
state.active=5;
results.resume=run('inspect');
results.resumeClick=run('carousel_dot',{
 carousel_index:1,stage_signature:results.resume.signature,
 carousel_signature:results.resume.carousel_signature});
results.afterResume=run('inspect');
for(const kind of ['hide_active','inert_active','expose_inactive',
 'uninert_inactive','wrong_dot_label','wrong_prev_label','second_current',
 'extra_button','slide_activity','hidden_action_link']){
 state.active=1;state.invalid=kind;
 results.negative[kind]=run('inspect');
 state.invalid=null;
}
state.active=1;state.invalid='hidden_citation';
results.hiddenCitation=run('inspect');
state.invalid=null;
const stable=run('inspect');
results.wrongSignature=run('carousel_dot',{
 carousel_index:2,stage_signature:stable.signature,
 carousel_signature:'changed'});
process.stdout.write(JSON.stringify(results));
'''
        result = subprocess.run(
            ["node", "-e", harness],
            input=json.dumps({
                "script": flow.INNER_JS, "frame": frame,
                "lessonUrl": lesson_url,
            }),
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        snapshots = json.loads(result.stdout)
        self.assertEqual(snapshots["initial"]["carousel_count"], 1)
        self.assertEqual(len(snapshots["initial"]["carousel_contents"]), 5)
        self.assertEqual(
            [entry["carousel_active_index"] for entry in snapshots["active"]],
            [1, 2, 3, 4, 5],
        )
        self.assertTrue(all(entry.get("clicked") == "carousel_dot"
                            for entry in snapshots["clicks"]))
        self.assertEqual(snapshots["resume"]["carousel_active_index"], 5)
        self.assertEqual(snapshots["resumeClick"]["clicked"], "carousel_dot")
        self.assertEqual(snapshots["afterResume"]["carousel_active_index"], 1)
        for kind, snapshot in snapshots["negative"].items():
            with self.subTest(kind=kind):
                self.assertIn("error", snapshot, snapshot)
        self.assertNotIn("error", snapshots["hiddenCitation"])
        self.assertEqual(snapshots["hiddenCitation"]["carousel_count"], 1)
        self.assertIn("error", snapshots["wrongSignature"])

    def test_content_links_allow_citations_but_reject_activity_controls(self):
        self.assertIn(flow.CONTENT_LINK_GUARD_JS, flow.INNER_JS)
        self.assertNotIn("__CONTENT_LINK_GUARD__", flow.INNER_JS)
        script = r"""
const guard = new Function('return ' + process.argv[1])();
const clean = value => (value || '').replace(/\s+/g, ' ').trim();
const page = {URL:'https://d10o6em2qtnr4q.cloudfront.net/assets/' +
  '0123456789abcdef0123456789abcdef/scorm-content/scormcontent/index.html#/lessons/lesson'};
function anchor(href, label, attributes={}, visible=true, className='') {
  return {innerText:label, textContent:label, className,
    getClientRects:()=>visible?[{}]:[],
    getAttribute:name=>name==='href'?href:attributes[name]??null,
    hasAttribute:name=>name==='href'||Object.hasOwn(attributes,name)};
}
const inspect = links => guard(page, {querySelectorAll:selector=>{
  if(selector!=='a[href]') throw new Error('Unexpected selector');
  return links;
}}, clean);
const cases = [
  ['citation', [anchor('https://example.edu/research', 'Research paper')], false],
  ['website', [anchor('https://example.edu/more', 'Read more')], false],
  ['reading navigation', [anchor('https://example.edu/reading', 'Continue reading')], false],
  ['fragment', [anchor('#references', 'References')], false],
  ['hidden launch', [anchor('https://example.edu/lab', 'Launch lab', {}, false)], false],
  ['launch', [anchor('https://example.edu/lab', 'Launch lab')], true],
  ['activity route', [anchor('https://example.edu/quiz/start', 'Click here')], true],
  ['activity continuation', [anchor('https://example.edu/activity', 'Continue to the activity')], true],
  ['button role', [anchor('https://example.edu/activity', 'Explore', {'role':'button'})], true],
  ['button class', [anchor('https://example.edu/activity', 'Explore', {}, true, 'primary-cta')], true],
  ['onclick', [anchor('https://example.edu/activity', 'Explore', {'onclick':'launch()'})], true],
  ['script', [anchor('javascript:launch()', 'More')], true],
  ['lesson route', [anchor('#/lessons/another', 'Next lesson')], true],
];
for (const [name, links, blocked] of cases) {
  const error = inspect(links);
  if (Boolean(error) !== blocked) throw new Error(name + ': ' + String(error));
}
"""
        result = subprocess.run(
            ["node", "-e", script, flow.CONTENT_LINK_GUARD_JS],
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_outer_browser_guard_requires_inner_proof_before_mark(self):
        script = """
const outer = new Function('return ' + process.argv[1])();
const lessonUrl = process.argv[2], frameUrl = process.argv[3];
let clicks = 0;
const heading = {textContent:'Defence techniques and strategies'};
const mark = {
  innerText:'Mark as completed', disabled:false,
  getAttribute:()=>null, closest:()=>null,
  scrollIntoView:()=>{}, click:()=>{clicks++}
};
const frame = {src:frameUrl, title:'Defence techniques and strategies'};
const root = {querySelectorAll:(selector) => {
  if (selector === 'iframe[data-testid="widget-iframe"]') return [frame];
  if (selector === 'button[data-testid="mark-complete"]') return [mark];
  if (selector === 'button,[role="button"]') return [mark];
  return [];
}};
global.location = {href:lessonUrl};
global.document = {
  readyState:'complete',
  querySelectorAll:(selector) => {
    if (selector === '#main-container') return [root];
    if (selector === 'h1') return [heading,heading];
    return [];
  }
};
const req = {
  path:new URL(lessonUrl).pathname,
  heading:'Defence techniques and strategies',
  frame:frameUrl, frame_title:'Defence techniques and strategies',
  action:'mark'
};
const blocked = outer({...req,inner_verified:false});
if (!blocked.error || clicks) process.exit(2);
const wrong = outer({...req,heading:'Another item',inner_verified:true});
if (!wrong.error || clicks) process.exit(3);
const allowed = outer({...req,inner_verified:true});
if (allowed.clicked !== 'mark' || clicks !== 1) process.exit(4);
"""
        result = subprocess.run(
            ["node", "-e", script, flow.OUTER_JS, ITEM["url"], FRAME],
            capture_output=True, text=True, check=False, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()

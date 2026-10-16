"""Offline checks for the audited static SCORM widget path."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
flow = importlib.import_module("scorm_flow")
plugins = importlib.import_module("plugin_flow")
engine = importlib.import_module("coursera_start")
COURSE = "https://www.coursera.org/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
ITEM = {"key": "ungradedWidget:3z0Im", "title": "Threat model Ungraded Plugin • 15 min",
        "url": "https://www.coursera.org" + flow.THREAT_MODEL_PATH}
REMARKS_ITEM = {"key": "ungradedWidget:2sbQI",
                "title": "Adversarial inputs Ungraded Plugin \uFFFD 25 min Resume",
                "url": "https://www.coursera.org" + flow.ADVERSARIAL_INPUTS_PATH}
EXERCISE_ITEM = {"key": "ungradedWidget:XMBvd",
                 "title": "Generating adversarial examples Ungraded Plugin • 30 min Resume",
                 "url": "https://www.coursera.org" + flow.GENERATING_EXAMPLES_PATH}
POISONING_ITEM = {"key": "ungradedWidget:UTogj",
                  "title": "Poisoning attacks Ungraded Plugin • 20 min Resume",
                  "url": "https://www.coursera.org" + flow.POISONING_PATH}
SUMMARY_ITEM = {"key": "ungradedWidget:SkhZ8",
                "title": "Summary Ungraded Plugin • 10 min Resume",
                "url": "https://www.coursera.org" + flow.SUMMARY_PATH}
OVERVIEW4_ITEM = {"key": "ungradedWidget:YFamB",
                  "title": "Overview Ungraded Plugin • 10 min Resume",
                  "url": "https://www.coursera.org" + flow.OVERVIEW4_PATH}
INTRODUCTION4_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/xBnh8/introduction"
)
INTRODUCTION4_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "98a1815dd12a428db3b9a6448325e7f2/index.html"
)
INTRODUCTION4_LESSON = "_mLgVvWjXWTqWjxfSRihYTCOqDrdou1h"
INTRODUCTION4_ITEM = {"key": "ungradedWidget:xBnh8",
                      "title": "Introduction Ungraded Plugin • 10 min",
                      "url": "https://www.coursera.org" + INTRODUCTION4_PATH}
DEFENCE4_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/gTEoB/defence-techniques-and-strategies"
)
DEFENCE4_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "7593944453f8458fb0f5c677ef10dc9d/index.html"
)
DEFENCE4_LESSON = "eih4-kXzAFPtZWonw92N1bgBl3WCi9fa"
DEFENCE4_ITEM = {"key": "ungradedWidget:gTEoB",
                 "title": "Defence techniques and strategies Ungraded Plugin • 30 min",
                 "url": "https://www.coursera.org" + DEFENCE4_PATH}
GENAI4_PATH = (
    "/learn/adversarial-ai-attacking-defending-and-governing-ml-systems-mqcsa"
    "/ungradedWidget/6uBZN/defences-for-attacks-on-genai-models"
)
GENAI4_FRAME = (
    "https://d10o6em2qtnr4q.cloudfront.net/assets/"
    "fb3d4742f7634ff2af1c80bd5a6bce4e/index.html"
)
GENAI4_LESSON = "duv5gTQ28HWXmT1REiSOU3TUgjnK2G_N"
GENAI4_ITEM = {"key": "ungradedWidget:6uBZN",
                "title": "Defences for attacks on GenAI models Ungraded Plugin • 20 min",
                "url": "https://www.coursera.org" + GENAI4_PATH}


class OuterTab:
    def __init__(self, *, confirm=True, frame=flow.THREAT_MODEL_FRAME):
        self.confirm = confirm
        self.frame = frame
        self.marked = False
        self.calls = []

    async def evaluate(self, expression):
        self.calls.append(expression)
        if '"action": "mark"' in expression:
            self.marked = True
            return {"clicked": "mark"}
        return {"ready": "complete", "frame": self.frame,
                "mark_count": 1, "mark_disabled": False, "modal": False, "other": []}


class InnerTab:
    def __init__(self, *, changed=False):
        self.top = 0
        self.changed = changed
        self.closed = False

    async def evaluate(self, expression):
        if '"action": "scroll"' in expression:
            self.top = min(900, self.top + 450)
        return {"url": flow.THREAT_MODEL_FRAME, "lesson": "LESSON 1 OF 1",
                "text_length": 5339 + int(self.changed and self.top > 0),
                "top": self.top, "max": 900, "progress": "0% COMPLETE"}

    async def close(self):
        self.closed = True


class RemarksTab(InnerTab):
    def __init__(self, *, interactive=False):
        super().__init__()
        self.expanded = False
        self.interactive = interactive
        self.actions = []

    async def evaluate(self, expression):
        if self.interactive:
            return {"error": "The SCORM lesson contains an interactive activity."}
        action = ("expand" if '"action": "expand"' in expression else
                  "scroll" if '"action": "scroll"' in expression else "inspect")
        self.actions.append(action)
        if action == "expand":
            self.expanded = True
        elif action == "scroll":
            self.top = min(3330, self.top + 600)
        return {"url": flow.ADVERSARIAL_INPUTS_FRAME, "lesson": "LESSON 1 OF 1",
                "text_length": 2464 if self.expanded else 2229,
                "top": self.top, "max": 3330 if self.expanded else 3100,
                "progress": "0% COMPLETE", "accordion_expanded": self.expanded,
                "clicked": "Remarks" if action == "expand" else None}


class ExerciseTab(InnerTab):
    def __init__(self, *, interactive=False, altered=False):
        super().__init__()
        self.stage = "reading"
        self.expanded = False
        self.interactive = interactive
        self.altered = altered
        self.actions = []

    async def evaluate(self, expression):
        action = ("expand" if '"action": "expand"' in expression else
                  "continue" if '"action": "continue"' in expression else
                  "scroll" if '"action": "scroll"' in expression else "inspect")
        self.actions.append(action)
        if self.interactive:
            return {"error": "The SCORM exercise contains an unhandled interactive control."}
        if action == "expand":
            self.expanded = True
        elif action == "scroll":
            self.top = min(8200 if self.stage == "continued" else 8000,
                           self.top + 700)
        elif action == "continue":
            if self.top != 8000:
                return {"error": "The exercise reading must finish before continuation."}
            self.stage = "continued"
            self.top = 0
        return {"url": flow.GENERATING_EXAMPLES_FRAME,
                "lesson": "LESSON 1 OF 1", "stage": self.stage,
                "text_length": (7455 if self.stage == "continued" else
                                7233 if self.expanded else 6880) +
                               int(self.altered and action == "scroll"),
                "top": self.top, "max": 8200 if self.stage == "continued" else 8000,
                "progress": "0% COMPLETE", "accordion_expanded": self.expanded,
                "clicked": ("Remarks" if action == "expand" else
                            "DO IT YOURSELF EXERCISE EIGHT" if action == "continue" else None)}


class PoisoningTab(InnerTab):
    def __init__(self, outer, *, interactive=False, changed=False):
        super().__init__(changed=changed)
        self.outer = outer
        self.stage = "reading"
        self.interactive = interactive
        self.actions = []

    async def evaluate(self, expression):
        action = ("continue" if '"action": "continue"' in expression else
                  "scroll" if '"action": "scroll"' in expression else "inspect")
        self.actions.append(action)
        if self.interactive:
            return {"error": "The poisoning lesson contains an unhandled interactive control."}
        if action == "scroll":
            self.top = min(2417 if self.stage == "continued" else 2341,
                           self.top + 500)
        elif action == "continue":
            if self.top != 2341:
                return {"error": "The poisoning lesson reading must finish before continuation."}
            self.stage = "continued"
            self.top = 0
            self.outer.marked = True  # This SCORM transition marks the Coursera item.
        return {"url": flow.POISONING_FRAME, "lesson": "LESSON 1 OF 1",
                "stage": self.stage,
                "text_length": (2945 if self.stage == "continued" else 2825) +
                               int(self.changed and action == "scroll"),
                "top": self.top, "max": 2417 if self.stage == "continued" else 2341,
                "progress": "0% COMPLETE",
                "clicked": "DO IT YOURSELF EXERCISE NINE" if action == "continue" else None}


class SummaryTab(InnerTab):
    def __init__(self, *, changed=False, extra_control=False):
        super().__init__(changed=changed)
        self.extra_control = extra_control
        self.actions = []

    async def evaluate(self, expression):
        action = "scroll" if '"action": "scroll"' in expression else "inspect"
        self.actions.append(action)
        if self.extra_control:
            return {"error": "The Summary lesson contains an unhandled interactive control."}
        if action == "scroll":
            self.top = min(2200, self.top + 500)
        return {"url": flow.SUMMARY_FRAME, "lesson": "LESSON 1 OF 1",
                "stage": "reading", "top": self.top, "max": 2200,
                "text_length": 3150 + int(self.changed and self.top > 0),
                "progress": "0% COMPLETE", "checkbox_count": 4}


class Overview4Tab(InnerTab):
    def __init__(self, *, changed=False, extra_control=False, stuck_scroll=False,
                 checkbox_count=4):
        super().__init__(changed=changed)
        self.extra_control = extra_control
        self.stuck_scroll = stuck_scroll
        self.checkbox_count = checkbox_count
        self.actions = []

    async def evaluate(self, expression):
        action = "scroll" if '"action": "scroll"' in expression else "inspect"
        self.actions.append(action)
        if self.extra_control:
            return {"error": "The Overview lesson contains an unhandled interactive control."}
        if action == "scroll" and not self.stuck_scroll:
            self.top = min(2200, self.top + 500)
        return {"url": flow.OVERVIEW4_FRAME, "lesson": "LESSON 1 OF 1",
                "stage": "reading", "top": self.top, "max": 2200,
                "text_length": 3200 + int(self.changed and self.top > 0),
                "progress": "0% COMPLETE", "checkbox_count": self.checkbox_count}


class Introduction4Tab(InnerTab):
    def __init__(self, *, stage="reading", flipped=0, extra_control=False,
                 changed=False, stuck_scroll=False, card_count=3):
        super().__init__(changed=changed)
        self.stage = stage
        self.flipped = flipped
        self.extra_control = extra_control
        self.stuck_scroll = stuck_scroll
        self.card_count = card_count
        self.actions = []

    async def evaluate(self, expression):
        action = ("continue" if '"action": "continue"' in expression else
                  "flip" if '"action": "flip"' in expression else
                  "scroll" if '"action": "scroll"' in expression else "inspect")
        self.actions.append(action)
        if self.extra_control:
            return {"error": "The Introduction lesson contains an unhandled interactive control."}
        max_scroll = 2202 if self.stage == "reading" else 4259
        clicked = None
        if action == "scroll" and not self.stuck_scroll:
            self.top = min(max_scroll, self.top + 600)
        elif action == "continue":
            if self.stage != "reading" or self.top < max_scroll - 2:
                return {"error": "The Introduction reading must finish before continuation."}
            self.stage = "continued"
            self.top = 0
            clicked = "CONTINUE"
        elif action == "flip":
            if self.stage != "continued" or self.flipped >= self.card_count:
                return {"error": "No unique Introduction flashcard is ready to flip."}
            self.flipped += 1
            clicked = "flip"
        return {"url": INTRODUCTION4_FRAME, "lesson": "LESSON 1 OF 1",
                "stage": self.stage, "top": self.top,
                "max": 2202 if self.stage == "reading" else 4259,
                "text_length": (2221 if self.stage == "reading" else 4844) +
                               int(self.changed and self.top > 0),
                "progress": "0% COMPLETE", "card_count": 0 if self.stage == "reading"
                else self.card_count, "flipped_count": self.flipped,
                "clicked": clicked}


class Defence4Tab(InnerTab):
    stages = ("intro", "evasion", "poisoning", "inversion", "scenario_intro",
              "scenario", "terminal")
    scroll_max = {"intro": 0, "evasion": 5621, "poisoning": 7604,
                  "inversion": 11400, "scenario_intro": 13000,
                  "scenario": 13000, "terminal": 13000}

    def __init__(self, *, stage="intro", scenario_step=0, changed=False,
                 unknown_control=False, wrong_feedback=False, stuck=False,
                 pending_after_action=0, always_pending=False,
                 initial_pending_probes=0, initial_always_pending=False):
        super().__init__(changed=changed)
        self.stage = stage
        self.scenario_step = scenario_step
        self.unknown_control = unknown_control
        self.wrong_feedback = wrong_feedback
        self.stuck = stuck
        self.pending_after_action = pending_after_action
        self.pending_probes = 0
        self.always_pending = always_pending
        self.initial_pending_probes = initial_pending_probes
        self.initial_always_pending = initial_always_pending
        self.initial_transition = bool(initial_pending_probes or initial_always_pending)
        self.expanded = 0
        self.flipped = 0
        self.actions = []

    def _snapshot(self, clicked=None):
        rank = self.stages.index(self.stage)
        scenario_state = ("terminal" if self.stage == "terminal" else
                          "choice" if self.stage == "scenario" and
                          self.scenario_step % 2 == 0 else
                          "continue" if self.stage == "scenario" else None)
        scenario_text = (f"scenario step {self.scenario_step}"
                         if self.stage == "scenario" else
                         "Scenario End I feel much more confident"
                         if self.stage == "terminal" else None)
        return {"url": DEFENCE4_FRAME, "lesson": "LESSON 1 OF 1",
                "progress": "0% COMPLETE", "stage": self.stage,
                "top": self.top, "max": self.scroll_max[self.stage],
                "text_length": 300 + rank * 4000 + int(self.changed and self.top > 0),
                "accordion_count": 3 if rank >= 2 else 0,
                "accordion_expanded": self.expanded,
                "card_count": 3 if rank >= 3 else 0,
                "flipped_count": self.flipped,
                "scenario_state": scenario_state,
                "scenario_text": scenario_text,
                "clicked": clicked}

    def _advance_stage(self):
        self.stage = self.stages[self.stages.index(self.stage) + 1]
        self.top = 0
        self.expanded = 0
        self.flipped = 0

    async def evaluate(self, expression):
        action = next((name for name in ("scenario_continue", "scenario_choice",
                                         "continue", "expand", "flip", "scroll")
                       if f'"action": "{name}"' in expression), "inspect")
        self.actions.append(action)
        if action == "inspect" and self.stage == "scenario_intro" and self.initial_transition:
            if self.initial_always_pending or self.initial_pending_probes > 0:
                self.initial_pending_probes = max(0, self.initial_pending_probes - 1)
                return {"pending": "The Defence techniques scenario is mounting."}
            self._advance_stage()
            self.initial_transition = False
        if action == "inspect" and self.stage == "scenario":
            if self.always_pending or self.pending_probes > 0:
                self.pending_probes = max(0, self.pending_probes - 1)
                return {"pending": "The Defence techniques scenario is transitioning."}
        if self.unknown_control:
            return {"error": "The Defence techniques lesson has an unhandled interactive control."}
        if self.wrong_feedback and self.stage == "scenario" and self.scenario_step == 1:
            return {"error": "The Defence techniques scenario feedback changed."}
        before = self._snapshot()
        if action == "scroll":
            self.top = min(self.scroll_max[self.stage], self.top + 550)
            return self._snapshot()
        if action == "expand":
            if self.expanded >= 3:
                return {"error": "No unopened Defence techniques accordion remains."}
            self.expanded += 1
            return {**before, "clicked": "expand"}
        if action == "flip":
            if self.expanded != 3 or self.flipped >= 3:
                return {"error": "No unviewed Defence techniques flashcard remains."}
            self.flipped += 1
            return {**before, "clicked": "flip"}
        if action == "continue":
            if self.top < self.scroll_max[self.stage] - 2:
                return {"error": "The Defence techniques stage is not ready to continue."}
            if not self.stuck:
                self._advance_stage()
            return {**before, "clicked": "scenario_start"
                    if before["stage"] == "scenario_intro" else "CONTINUE"}
        if action in {"scenario_choice", "scenario_continue"}:
            if self.stage != "scenario":
                return {"error": "The Defence techniques scenario has no audited action."}
            if not self.stuck:
                if self.scenario_step == 8:
                    self._advance_stage()
                else:
                    self.scenario_step += 1
                    self.pending_probes = self.pending_after_action
            return {**before, "clicked": action}
        return before


class GenAI4Tab(InnerTab):
    scroll_max = {"reading": 3700, "scenario_intro": 4955,
                  "scenario": 6200, "terminal": 6233}

    def __init__(self, *, stage="reading", scenario_step=0, changed=False,
                 unknown_control=False, wrong_feedback=False, stuck=False):
        super().__init__(changed=changed)
        self.stage = stage
        self.scenario_step = scenario_step
        self.unknown_control = unknown_control
        self.wrong_feedback = wrong_feedback
        self.stuck = stuck
        self.open_index = -1
        self.visited = set()
        self.actions = []

    def _snapshot(self, clicked=None):
        scenario_state = ("terminal" if self.stage == "terminal" else
                          "choice" if self.stage == "scenario" and
                          self.scenario_step % 2 == 0 else
                          "continue" if self.stage == "scenario" else None)
        scenario_text = (f"GenAI scenario step {self.scenario_step}"
                         if self.stage == "scenario" else
                         "Scenario End You've helped me grasp the challenges of LLM security"
                         if self.stage == "terminal" else None)
        stage_lengths = {"reading": 3879, "scenario_intro": 5309,
                         "scenario": 6100, "terminal": 6260}
        return {"url": GENAI4_FRAME, "lesson": "LESSON 1 OF 1",
                "progress": "0% COMPLETE", "stage": self.stage,
                "top": self.top, "max": self.scroll_max[self.stage],
                "text_length": stage_lengths[self.stage] +
                               int(self.changed and self.top > 0),
                "accordion_count": 5, "accordion_open_index": self.open_index,
                "scenario_state": scenario_state, "scenario_text": scenario_text,
                "clicked": clicked}

    async def evaluate(self, expression):
        action = next((name for name in ("scenario_continue", "scenario_choice",
                                         "open_accordion", "continue", "scroll")
                       if f'"action": "{name}"' in expression), "inspect")
        self.actions.append(action)
        if self.unknown_control:
            return {"error": "The GenAI lesson has an unhandled interactive control."}
        if self.wrong_feedback and self.stage == "scenario" and self.scenario_step == 1:
            return {"error": "The GenAI scenario feedback changed."}
        before = self._snapshot()
        if action == "scroll":
            self.top = min(self.scroll_max[self.stage], self.top + 550)
            return self._snapshot()
        if action == "open_accordion":
            index = next((i for i in range(5) if f'"index": {i}' in expression), None)
            if index is None or index in self.visited:
                return {"error": "The GenAI accordion selection changed."}
            self.open_index = index
            self.visited.add(index)
            return {**before, "clicked": "open_accordion"}
        if action == "continue":
            if self.top < self.scroll_max[self.stage] - 2 or self.stage == "reading" and len(self.visited) < 5:
                return {"error": "The GenAI stage is not ready to continue."}
            if not self.stuck:
                self.stage = "scenario_intro" if self.stage == "reading" else "scenario"
                self.top = 0
                self.open_index = -1
            return {**before, "clicked": "scenario_start"
                    if before["stage"] == "scenario_intro" else "CONTINUE"}
        if action in {"scenario_choice", "scenario_continue"}:
            if self.stage != "scenario":
                return {"error": "The GenAI scenario has no audited action."}
            if not self.stuck:
                if self.scenario_step == 10:
                    self.stage = "terminal"
                    self.top = 0
                else:
                    self.scenario_step += 1
            return {**before, "clicked": action}
        return before


class Runner:
    def __init__(self, tab):
        self.tab = tab
        self.course = {"url": COURSE}
        self.events = []

    async def verified_item(self, item, wait=0):
        return self.tab.marked and self.tab.confirm

    def event(self, name, **data):
        self.events.append((name, data))


class StaticScormTests(unittest.IsolatedAsyncioTestCase):
    def test_only_audited_exact_lesson_can_enter_static_flow(self):
        self.assertTrue(flow.is_static_scorm(flow.THREAT_MODEL_PATH, "Threat model"))
        self.assertTrue(flow.is_static_scorm(flow.ADVERSARIAL_INPUTS_PATH,
                                             "Adversarial inputs"))
        self.assertTrue(flow.is_static_scorm(flow.GENERATING_EXAMPLES_PATH,
                                             "Generating adversarial examples"))
        self.assertTrue(flow.is_static_scorm(flow.POISONING_PATH, "Poisoning attacks"))
        self.assertTrue(flow.is_static_scorm(flow.SUMMARY_PATH, "Summary"))
        self.assertTrue(flow.is_static_scorm(flow.OVERVIEW4_PATH, "Overview"))
        self.assertFalse(flow.is_static_scorm(flow.THREAT_MODEL_PATH, "Harmful outputs"))
        self.assertFalse(flow.is_static_scorm(
            flow.ADVERSARIAL_INPUTS_PATH.replace("2sbQI", "other"), "Adversarial inputs"))
        self.assertFalse(flow.is_static_scorm(flow.THREAT_MODEL_PATH.replace("3z0Im", "other"),
                                             "Threat model"))
        self.assertEqual(plugins.plugin_path(ITEM, COURSE), flow.THREAT_MODEL_PATH)
        self.assertEqual(plugins.plugin_path(REMARKS_ITEM, COURSE), flow.ADVERSARIAL_INPUTS_PATH)
        self.assertEqual(plugins.plugin_path(EXERCISE_ITEM, COURSE),
                         flow.GENERATING_EXAMPLES_PATH)
        self.assertEqual(plugins.plugin_path(POISONING_ITEM, COURSE), flow.POISONING_PATH)
        self.assertEqual(plugins.plugin_path(SUMMARY_ITEM, COURSE), flow.SUMMARY_PATH)
        self.assertEqual(plugins.plugin_path(OVERVIEW4_ITEM, COURSE), flow.OVERVIEW4_PATH)
        self.assertFalse(flow.is_static_scorm(flow.SUMMARY_PATH.replace("SkhZ8", "other"),
                                              "Summary"))
        self.assertFalse(flow.is_static_scorm(flow.SUMMARY_PATH, "Topic 3: Summary"))
        self.assertFalse(flow.is_static_scorm(flow.OVERVIEW4_PATH.replace("YFamB", "other"),
                                              "Overview"))
        self.assertFalse(flow.is_static_scorm(flow.OVERVIEW4_PATH, "Topic 4: Overview"))

    def test_introduction4_routes_only_the_audited_page(self):
        self.assertEqual(flow.INTRODUCTION4_PATH, INTRODUCTION4_PATH)
        self.assertEqual(flow.INTRODUCTION4_FRAME, INTRODUCTION4_FRAME)
        self.assertEqual(flow.INTRODUCTION4_LESSON, INTRODUCTION4_LESSON)
        self.assertTrue(flow.is_static_scorm(INTRODUCTION4_PATH, "Introduction"))
        self.assertFalse(flow.is_static_scorm(
            INTRODUCTION4_PATH.replace("xBnh8", "other"), "Introduction"))
        self.assertFalse(flow.is_static_scorm(INTRODUCTION4_PATH, "Topic 4: Introduction"))
        self.assertEqual(plugins.plugin_path(INTRODUCTION4_ITEM, COURSE), INTRODUCTION4_PATH)

    def test_defence4_routes_only_the_audited_page(self):
        self.assertEqual(flow.DEFENCE4_PATH, DEFENCE4_PATH)
        self.assertEqual(flow.DEFENCE4_FRAME, DEFENCE4_FRAME)
        self.assertEqual(flow.DEFENCE4_LESSON, DEFENCE4_LESSON)
        self.assertTrue(flow.is_static_scorm(DEFENCE4_PATH, "Defence techniques and strategies"))
        self.assertFalse(flow.is_static_scorm(
            DEFENCE4_PATH.replace("gTEoB", "other"), "Defence techniques and strategies"))
        self.assertFalse(flow.is_static_scorm(DEFENCE4_PATH, "Topic 4: Defence techniques"))
        self.assertEqual(plugins.plugin_path(DEFENCE4_ITEM, COURSE), DEFENCE4_PATH)

    def test_genai4_routes_only_the_audited_page(self):
        self.assertEqual(flow.GENAI4_PATH, GENAI4_PATH)
        self.assertEqual(flow.GENAI4_FRAME, GENAI4_FRAME)
        self.assertEqual(flow.GENAI4_LESSON, GENAI4_LESSON)
        self.assertTrue(flow.is_static_scorm(GENAI4_PATH, "Defences for attacks on GenAI models"))
        self.assertFalse(flow.is_static_scorm(
            GENAI4_PATH.replace("6uBZN", "other"), "Defences for attacks on GenAI models"))
        self.assertFalse(flow.is_static_scorm(GENAI4_PATH, "Defences for attacks on GenAI model"))
        self.assertEqual(plugins.plugin_path(GENAI4_ITEM, COURSE), GENAI4_PATH)

    async def test_iframe_connection_requires_exact_parent_and_asset(self):
        class Owner:
            address = "ws://127.0.0.1:9223/devtools/page/EXPECTED"

            def __init__(self, parent):
                self.parent = parent

            async def call(self, method):
                self.assert_method = method
                return {"targetInfos": [{"type": "iframe", "parentId": self.parent,
                                         "targetId": "FRAME", "url": flow.THREAT_MODEL_FRAME}]}

        class FrameSocket:
            connected = False

            async def connect(self):
                self.connected = True

        socket = FrameSocket()
        pages = [{"type": "iframe", "id": "FRAME", "url": flow.THREAT_MODEL_FRAME,
                  "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/FRAME"}]
        with patch.object(engine, "local_json", return_value=pages), \
             patch.object(engine, "ChromeTab", return_value=socket):
            with self.assertRaisesRegex(engine.StopRun, "unique audited SCORM iframe"):
                await flow.open_scorm_frame(Owner("ANOTHER_TAB"), probes=1)
            result = await flow.open_scorm_frame(Owner("EXPECTED"))
        self.assertIs(result, socket)
        self.assertTrue(socket.connected)

    async def test_iframe_connection_waits_for_exact_target_and_socket(self):
        class Owner:
            address = "ws://127.0.0.1:9223/devtools/page/EXPECTED"
            calls = 0

            async def call(self, method):
                self.calls += 1
                if self.calls == 1:
                    return {"targetInfos": []}
                return {"targetInfos": [{"type": "iframe", "parentId": "EXPECTED",
                         "targetId": "FRAME", "url": flow.THREAT_MODEL_FRAME}]}

        class FrameSocket:
            connected = False

            async def connect(self):
                self.connected = True

        owner, socket = Owner(), FrameSocket()
        pages = [[], [{"type": "iframe", "id": "FRAME",
                       "url": flow.THREAT_MODEL_FRAME,
                       "webSocketDebuggerUrl": "ws://127.0.0.1:9223/devtools/page/FRAME"}]]
        with patch.object(engine, "local_json", side_effect=pages), \
             patch.object(engine, "ChromeTab", return_value=socket), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()) as sleep:
            result = await flow.open_scorm_frame(owner, probes=4)
        self.assertIs(result, socket)
        self.assertTrue(socket.connected)
        self.assertEqual(owner.calls, 3)
        self.assertEqual(sleep.await_count, 2)

    async def test_iframe_connection_rejects_ambiguous_exact_targets(self):
        class Owner:
            address = "ws://127.0.0.1:9223/devtools/page/EXPECTED"

            async def call(self, method):
                return {"targetInfos": [{"type": "iframe", "parentId": "EXPECTED",
                         "targetId": target, "url": flow.THREAT_MODEL_FRAME}
                        for target in ("FRAME_1", "FRAME_2")]}

        with patch.object(engine, "local_json") as pages:
            with self.assertRaisesRegex(engine.StopRun, "unique audited SCORM iframe"):
                await flow.open_scorm_frame(Owner(), probes=4)
        pages.assert_not_called()

    async def test_plugin_routing_reads_static_widget_before_mark(self):
        tab, frame = OuterTab(), InnerTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), ITEM)
        self.assertTrue(frame.closed)
        self.assertEqual(frame.top, 900)
        self.assertTrue(tab.marked)
        self.assertEqual(len(tab.calls), 2)

    async def test_changed_content_stops_before_coursera_mark(self):
        tab, frame = OuterTab(), InnerTab(changed=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "reading area changed"):
                await plugins.complete_plugin(Runner(tab), ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_remarks_are_expanded_and_read_before_mark(self):
        tab = OuterTab(frame=flow.ADVERSARIAL_INPUTS_FRAME)
        frame = RemarksTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), REMARKS_ITEM)
        self.assertEqual(frame.actions[:3], ["inspect", "expand", "inspect"])
        self.assertEqual(frame.top, 3330)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)

    async def test_interactive_remarks_widget_cannot_be_marked(self):
        tab = OuterTab(frame=flow.ADVERSARIAL_INPUTS_FRAME)
        frame = RemarksTab(interactive=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)):
            with self.assertRaisesRegex(engine.StopRun, "interactive activity"):
                await plugins.complete_plugin(Runner(tab), REMARKS_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_scorm_exercise_reads_both_screens_before_mark(self):
        tab = OuterTab(frame=flow.GENERATING_EXAMPLES_FRAME)
        frame = ExerciseTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), EXERCISE_ITEM)
        self.assertEqual(frame.actions[:3], ["inspect", "expand", "inspect"])
        self.assertIn("continue", frame.actions)
        self.assertEqual(frame.stage, "continued")
        self.assertEqual(frame.top, 8200)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)

    async def test_scorm_exercise_unknown_control_stops_before_mark(self):
        tab = OuterTab(frame=flow.GENERATING_EXAMPLES_FRAME)
        frame = ExerciseTab(interactive=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)):
            with self.assertRaisesRegex(engine.StopRun, "interactive control"):
                await plugins.complete_plugin(Runner(tab), EXERCISE_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_scorm_exercise_changed_reading_stops_before_mark(self):
        tab = OuterTab(frame=flow.GENERATING_EXAMPLES_FRAME)
        frame = ExerciseTab(altered=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while"):
                await plugins.complete_plugin(Runner(tab), EXERCISE_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_poisoning_reads_both_screens_and_requires_automatic_green_mark(self):
        tab = OuterTab(frame=flow.POISONING_FRAME)
        frame = PoisoningTab(tab)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), POISONING_ITEM)
        self.assertEqual(frame.actions[0], "inspect")
        self.assertIn("continue", frame.actions)
        self.assertEqual(frame.stage, "continued")
        self.assertEqual(frame.top, 2417)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)
        self.assertEqual(len(tab.calls), 1)  # No extra Coursera Mark click.

    async def test_poisoning_unhandled_control_stops_before_continuation(self):
        tab = OuterTab(frame=flow.POISONING_FRAME)
        frame = PoisoningTab(tab, interactive=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)):
            with self.assertRaisesRegex(engine.StopRun, "unhandled interactive control"):
                await plugins.complete_plugin(Runner(tab), POISONING_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_poisoning_changed_reading_stops_before_continuation(self):
        tab = OuterTab(frame=flow.POISONING_FRAME)
        frame = PoisoningTab(tab, changed=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while"):
                await plugins.complete_plugin(Runner(tab), POISONING_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_summary_reads_all_text_then_marks_and_requires_green(self):
        tab = OuterTab(frame=flow.SUMMARY_FRAME)
        frame = SummaryTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), SUMMARY_ITEM)
        self.assertEqual(frame.top, 2200)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)
        self.assertEqual(len(tab.calls), 2)  # Inspect and Coursera Mark; no checkbox click.
        self.assertTrue(frame.actions)
        self.assertEqual(frame.actions[0], "inspect")
        self.assertEqual(set(frame.actions), {"inspect", "scroll"})

    async def test_summary_changed_content_never_marks(self):
        tab = OuterTab(frame=flow.SUMMARY_FRAME)
        frame = SummaryTab(changed=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while"):
                await plugins.complete_plugin(Runner(tab), SUMMARY_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_summary_extra_control_never_marks(self):
        tab = OuterTab(frame=flow.SUMMARY_FRAME)
        frame = SummaryTab(extra_control=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)):
            with self.assertRaisesRegex(engine.StopRun, "interactive control"):
                await plugins.complete_plugin(Runner(tab), SUMMARY_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_summary_mark_without_green_is_not_completion(self):
        tab = OuterTab(frame=flow.SUMMARY_FRAME, confirm=False)
        frame = SummaryTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "green completion mark"):
                await plugins.complete_plugin(Runner(tab), SUMMARY_ITEM)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)

    async def test_overview4_reads_all_text_then_marks_and_requires_green(self):
        tab = OuterTab(frame=flow.OVERVIEW4_FRAME)
        frame = Overview4Tab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), OVERVIEW4_ITEM)
        self.assertEqual(frame.top, 2200)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)
        self.assertEqual(len(tab.calls), 2)
        self.assertEqual(frame.actions[0], "inspect")
        self.assertEqual(set(frame.actions), {"inspect", "scroll"})

    async def test_overview4_changed_content_never_marks(self):
        tab = OuterTab(frame=flow.OVERVIEW4_FRAME)
        frame = Overview4Tab(changed=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while"):
                await plugins.complete_plugin(Runner(tab), OVERVIEW4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_overview4_unexpected_control_never_marks(self):
        tab = OuterTab(frame=flow.OVERVIEW4_FRAME)
        frame = Overview4Tab(extra_control=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)):
            with self.assertRaisesRegex(engine.StopRun, "interactive control"):
                await plugins.complete_plugin(Runner(tab), OVERVIEW4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_overview4_must_reach_end_before_mark(self):
        tab = OuterTab(frame=flow.OVERVIEW4_FRAME)
        frame = Overview4Tab(stuck_scroll=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while|reach its end"):
                await plugins.complete_plugin(Runner(tab), OVERVIEW4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_overview4_missing_green_is_not_completion(self):
        tab = OuterTab(frame=flow.OVERVIEW4_FRAME, confirm=False)
        frame = Overview4Tab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "green completion mark"):
                await plugins.complete_plugin(Runner(tab), OVERVIEW4_ITEM)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)

    async def test_overview4_fifth_checkbox_never_marks(self):
        tab = OuterTab(frame=flow.OVERVIEW4_FRAME)
        frame = Overview4Tab(checkbox_count=5)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "not read to the end|self-check"):
                await plugins.complete_plugin(Runner(tab), OVERVIEW4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_introduction4_reads_both_stages_flips_three_cards_then_marks(self):
        tab = OuterTab(frame=INTRODUCTION4_FRAME)
        frame = Introduction4Tab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), INTRODUCTION4_ITEM)
        self.assertEqual(frame.actions[0], "inspect")
        self.assertEqual(frame.actions.count("continue"), 1)
        self.assertEqual(frame.actions.count("flip"), 3)
        self.assertEqual(frame.stage, "continued")
        self.assertEqual(frame.flipped, 3)
        self.assertEqual(frame.top, 4259)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)
        self.assertEqual(len(tab.calls), 2)

    async def test_introduction4_resumes_partly_flipped_second_stage(self):
        tab = OuterTab(frame=INTRODUCTION4_FRAME)
        frame = Introduction4Tab(stage="continued", flipped=1)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), INTRODUCTION4_ITEM)
        self.assertNotIn("continue", frame.actions)
        self.assertEqual(frame.actions.count("flip"), 2)
        self.assertEqual(frame.top, 4259)
        self.assertTrue(tab.marked)

    async def test_introduction4_changed_text_stops_before_mark(self):
        tab = OuterTab(frame=INTRODUCTION4_FRAME)
        frame = Introduction4Tab(changed=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while"):
                await plugins.complete_plugin(Runner(tab), INTRODUCTION4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_introduction4_unknown_control_stops_before_mark(self):
        tab = OuterTab(frame=INTRODUCTION4_FRAME)
        frame = Introduction4Tab(extra_control=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)):
            with self.assertRaisesRegex(engine.StopRun, "interactive control"):
                await plugins.complete_plugin(Runner(tab), INTRODUCTION4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_introduction4_short_read_stops_before_mark(self):
        tab = OuterTab(frame=INTRODUCTION4_FRAME)
        frame = Introduction4Tab(stuck_scroll=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while|reach its end"):
                await plugins.complete_plugin(Runner(tab), INTRODUCTION4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_introduction4_fourth_card_stops_before_mark(self):
        tab = OuterTab(frame=INTRODUCTION4_FRAME)
        frame = Introduction4Tab(stage="continued", card_count=4)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "card|flashcard"):
                await plugins.complete_plugin(Runner(tab), INTRODUCTION4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_introduction4_mark_without_green_is_not_completion(self):
        tab = OuterTab(frame=INTRODUCTION4_FRAME, confirm=False)
        frame = Introduction4Tab(stage="continued", flipped=3)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "green completion mark"):
                await plugins.complete_plugin(Runner(tab), INTRODUCTION4_ITEM)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)

    async def test_defence4_finishes_all_stages_and_recovers_reading_activities(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab()
        runner = Runner(tab)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(runner, DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)
        self.assertEqual(frame.stage, "terminal")
        self.assertEqual(frame.top, frame.scroll_max["terminal"])
        self.assertEqual(frame.actions.count("expand"), 15)
        self.assertEqual(frame.actions.count("flip"), 12)
        self.assertEqual(frame.actions.count("scenario_choice"), 5)
        self.assertEqual(frame.actions.count("scenario_continue"), 4)
        self.assertEqual(frame.actions.count("continue"), 5)
        self.assertNotIn("start_over", frame.actions)
        self.assertEqual(sum(name == "scorm_defence4_mark_clicked"
                             for name, _ in runner.events), 1)

    async def test_defence4_resumes_in_the_scenario_and_checks_terminal(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="scenario", scenario_step=4)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)
        self.assertEqual(frame.stage, "terminal")
        self.assertEqual(frame.actions.count("continue"), 0)
        self.assertEqual(frame.actions.count("scenario_choice"), 3)
        self.assertEqual(frame.actions.count("scenario_continue"), 2)
        self.assertEqual(frame.actions.count("expand"), 6)
        self.assertEqual(frame.actions.count("flip"), 6)

    async def test_defence4_unknown_control_stops_before_mark(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="poisoning", unknown_control=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "interactive control"):
                await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_defence4_changed_reading_stops_before_mark(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="evasion", changed=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while"):
                await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_defence4_wrong_feedback_stops_before_mark(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="scenario", wrong_feedback=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "feedback changed"):
                await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_defence4_action_without_progress_stops_before_mark(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="scenario", stuck=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "did not advance"):
                await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)
        self.assertLess(frame.actions.count("inspect"), 40)

    async def test_defence4_brief_scenario_transition_recovers(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="scenario", scenario_step=4,
                            pending_after_action=3)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(tab.marked)
        self.assertTrue(frame.closed)
        self.assertEqual(frame.stage, "terminal")
        self.assertGreater(frame.actions.count("inspect"), 15)

    async def test_defence4_transition_stays_pending_only_for_bounded_time(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="scenario", always_pending=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "did not finish loading"):
                await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)
        self.assertEqual(frame.actions.count("inspect"), 40)

    async def test_defence4_resume_during_scenario_mount_recovers(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="scenario_intro", initial_pending_probes=3)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(tab.marked)
        self.assertTrue(frame.closed)
        self.assertEqual(frame.stage, "terminal")
        self.assertEqual(frame.actions.count("continue"), 0)
        self.assertEqual(frame.actions.count("scenario_choice"), 5)

    async def test_defence4_scenario_mount_is_bounded(self):
        tab = OuterTab(frame=DEFENCE4_FRAME)
        frame = Defence4Tab(stage="scenario_intro", initial_always_pending=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "did not finish loading"):
                await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)
        self.assertEqual(frame.actions.count("inspect"), 40)

    async def test_defence4_mark_without_green_is_not_completion(self):
        tab = OuterTab(frame=DEFENCE4_FRAME, confirm=False)
        frame = Defence4Tab(stage="terminal")
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "green mark"):
                await plugins.complete_plugin(Runner(tab), DEFENCE4_ITEM)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)

    async def test_genai4_visits_five_exclusive_accordions_and_finishes_scenario(self):
        tab = OuterTab(frame=GENAI4_FRAME)
        frame = GenAI4Tab()
        runner = Runner(tab)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(runner, GENAI4_ITEM)
        self.assertTrue(tab.marked)
        self.assertTrue(frame.closed)
        self.assertEqual(frame.visited, set(range(5)))
        self.assertEqual(frame.stage, "terminal")
        self.assertEqual(frame.top, frame.scroll_max["terminal"])
        self.assertEqual(frame.actions.count("scenario_choice"), 6)
        self.assertEqual(frame.actions.count("scenario_continue"), 5)
        self.assertEqual(sum(name == "scorm_genai4_mark_clicked"
                             for name, _ in runner.events), 1)

    async def test_genai4_resumes_active_scenario_before_mark(self):
        tab = OuterTab(frame=GENAI4_FRAME)
        frame = GenAI4Tab(stage="scenario", scenario_step=4)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            await plugins.complete_plugin(Runner(tab), GENAI4_ITEM)
        self.assertTrue(tab.marked)
        self.assertTrue(frame.closed)
        self.assertEqual(frame.stage, "terminal")
        self.assertNotIn("continue", frame.actions)
        self.assertEqual(frame.actions.count("scenario_choice"), 4)
        self.assertEqual(frame.actions.count("scenario_continue"), 3)

    async def test_genai4_unknown_control_stops_before_mark(self):
        tab = OuterTab(frame=GENAI4_FRAME)
        frame = GenAI4Tab(unknown_control=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "interactive control"):
                await plugins.complete_plugin(Runner(tab), GENAI4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_genai4_changed_reading_stops_before_mark(self):
        tab = OuterTab(frame=GENAI4_FRAME)
        frame = GenAI4Tab(changed=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "changed while"):
                await plugins.complete_plugin(Runner(tab), GENAI4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_genai4_wrong_scenario_feedback_stops_before_mark(self):
        tab = OuterTab(frame=GENAI4_FRAME)
        frame = GenAI4Tab(stage="scenario", wrong_feedback=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "feedback changed"):
                await plugins.complete_plugin(Runner(tab), GENAI4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)

    async def test_genai4_no_progress_is_bounded_and_never_marks(self):
        tab = OuterTab(frame=GENAI4_FRAME)
        frame = GenAI4Tab(stage="scenario", stuck=True)
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "did not advance"):
                await plugins.complete_plugin(Runner(tab), GENAI4_ITEM)
        self.assertTrue(frame.closed)
        self.assertFalse(tab.marked)
        self.assertLess(frame.actions.count("inspect"), 50)

    async def test_genai4_mark_without_green_is_not_completion(self):
        tab = OuterTab(frame=GENAI4_FRAME, confirm=False)
        frame = GenAI4Tab(stage="terminal")
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "green mark"):
                await plugins.complete_plugin(Runner(tab), GENAI4_ITEM)
        self.assertTrue(frame.closed)
        self.assertTrue(tab.marked)

    async def test_coursera_green_mark_is_required_after_click(self):
        tab, frame = OuterTab(confirm=False), InnerTab()
        with patch.object(flow, "open_scorm_frame", new=AsyncMock(return_value=frame)), \
             patch.object(flow.asyncio, "sleep", new=AsyncMock()):
            with self.assertRaisesRegex(engine.StopRun, "green completion mark"):
                await plugins.complete_plugin(Runner(tab), ITEM)
        self.assertTrue(tab.marked)

    def test_inner_script_accepts_only_static_one_lesson_content(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const wrap={innerText:'A'.repeat(5339),scrollHeight:1500,clientHeight:600,scrollTop:0};
const page={readyState:'complete',querySelectorAll:selector=>
  selector==='div.page-wrap'?[wrap]:
  selector==='button,[role="button"]' && data.interactive?[{}]:[],
  querySelector:selector=>null};
const frame={contentDocument:page};
const labels=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu'];
if(data.continue) labels.push('CONTINUE');
const controls=labels.map(innerText=>({innerText,textContent:innerText}));
const document={readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
  querySelectorAll:selector=>selector==='iframe#content-frame'?[frame]:
    selector==='button,[role="button"]'?controls:
    selector==='input'?[{type:'search'}]:[],
  querySelector:selector=>selector==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:null};
const result=vm.runInNewContext(data.script+'('+JSON.stringify(data.request)+')',
  {document,location:{href:data.url}});
process.stdout.write(JSON.stringify({result,top:wrap.scrollTop}));
'''
        for case in ({"interactive": False, "continue": False, "accepted": True},
                     {"interactive": True, "continue": False, "accepted": False},
                     {"interactive": False, "continue": True, "accepted": False}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.INNER_JS, "url": flow.THREAT_MODEL_FRAME,
                    "request": {"frame": flow.THREAT_MODEL_FRAME, "action": "scroll",
                                "accordion": None}}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual("error" not in value["result"], case["accepted"])
                self.assertEqual(value["top"] > 0, case["accepted"])

    def test_remarks_script_rejects_other_controls_and_checks_expanded_panel(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let expanded=false;
const wrap={clientHeight:500,scrollTop:0,
  get innerText(){return 'A'.repeat(expanded?2464:2229)},
  get scrollHeight(){return expanded?3830:3600}};
const panel={innerText:'B'.repeat(data.shortPanel?20:233),
  matches:selector=>selector==='section.blocks-accordion__content',
  getAttribute:name=>name==='aria-labelledby'?'title-dynamic':null,
  querySelector:()=>null};
const remarks={innerText:'Remarks',type:'button',closest:selector=>selector==='h2'?{}:null,
  getAttribute:name=>name==='aria-controls'?'panel-dynamic':
    name==='aria-expanded'?(expanded?'true':'false'):null,
  click:()=>{expanded=true}};
const page={readyState:'complete',getElementById:id=>id==='panel-dynamic'?panel:null,
  querySelectorAll:selector=>selector==='div.page-wrap'?[wrap]:
    selector==='button.blocks-accordion__header'?[remarks]:
    selector==='button,[role="button"]'?[remarks,...(data.extra?[{}]:[])]:[],
  querySelector:()=>null};
const frame={contentDocument:page};
const controls=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']
  .map(innerText=>({innerText}));
const document={readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
  querySelectorAll:selector=>selector==='iframe#content-frame'?[frame]:
    selector==='button,[role="button"]'?controls:
    selector==='input'?[{type:'search',disabled:true}]:[],
  querySelector:selector=>selector==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:null};
const context={document,location:{href:data.frame}};
const run=action=>vm.runInNewContext(data.script+'('+JSON.stringify({
  frame:data.frame,accordion:'Remarks',action})+')',context);
const before=run('inspect'),open=run('expand'),after=run('inspect'),read=run('scroll');
process.stdout.write(JSON.stringify({before,open,after,read,top:wrap.scrollTop}));
'''
        for case in ({"extra": False, "shortPanel": False, "accepted": True},
                     {"extra": True, "shortPanel": False, "accepted": False},
                     {"extra": False, "shortPanel": True, "accepted": False}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.INNER_JS, "frame": flow.ADVERSARIAL_INPUTS_FRAME}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                if case["accepted"]:
                    self.assertEqual(value["before"]["text_length"], 2229)
                    self.assertEqual(value["open"]["clicked"], "Remarks")
                    self.assertTrue(value["after"]["accordion_expanded"])
                    self.assertEqual(value["after"]["text_length"], 2464)
                    self.assertGreater(value["top"], 0)
                else:
                    self.assertTrue(any("error" in value[action] for action in
                                        ("before", "open", "after", "read")))
                    self.assertEqual(value["top"], 0)

    def test_exercise_script_checks_both_nested_screens_and_rejects_new_controls(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let stage='reading',expanded=false,top=0;
const base=data.frame.replace(/index\.html$/,'');
const button=(text,extra={})=>({innerText:text,getClientRects:()=>[1],...extra});
const chrome=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']
  .map(text=>button(text));
const remarks=button('Remarks',{
  type:'button',closest:selector=>selector==='h2'?{}:null,
  getAttribute:name=>name==='aria-controls'?'panel-dynamic':
    name==='aria-expanded'?(expanded?'true':'false'):null,
  click:()=>{expanded=true}});
const cont=button('DO IT YOURSELF EXERCISE EIGHT',{click:()=>{stage='continued';top=0}});
const zoom=[button('Zoom image'),button('Zoom image')];
const extra=button('Submit answer');
const search={type:'search',disabled:true};
const form={matches:selector=>selector==='form.nav-sidebar-search--disabled',
  querySelectorAll:selector=>selector==='input'?[search]:[]};
const panel={innerText:'Explanation '.repeat(30),
  matches:selector=>selector==='section.blocks-accordion__content',querySelector:()=>null};
const wrap={get innerText(){return 'fgsm_attack plt.plot(epsilons, accuracies '+
  'A'.repeat(7100)+(expanded?' Remarks explanation '.repeat(20):'')+
  (stage==='continued'?' In this eighth do it yourself exercise there are no solution to this exercise.':'')},
  clientHeight:500,get scrollHeight(){return (stage==='continued'?8200:8000)+500},
  get scrollTop(){return top},set scrollTop(value){top=value}};
const page={URL:base+'scorm-content/scormcontent/index.html#/lessons/'+data.lesson,
  readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
  getElementById:id=>id==='panel-dynamic'?panel:null,
  querySelectorAll:selector=>({
    'iframe':[],'div.page-wrap':[wrap],
    'button.blocks-accordion__header':[remarks],
    'button[data-continue-btn]':stage==='reading'?[cont]:[],
    'button[data-rmiz-btn-zoom]':stage==='reading'?zoom:[],
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close':chrome,
    'button,[role="button"]':[...chrome,remarks,...(stage==='reading'?[cont,...zoom]:[]),
      ...(data.extra?[extra]:[])],
    'input':[search],'form':[form]
  }[selector]||[]),
  querySelector:selector=>selector==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:
    selector==='input[type="search"][disabled]'?search:null};
if(data.extraInput)page.querySelectorAll=(original=>selector=>
  selector==='input'?[search,{}]:original(selector))(page.querySelectorAll);
const content={title:'Generating adversarial examples',contentDocument:page};
const driver={readyState:'complete',querySelectorAll:selector=>
  selector==='iframe#content-frame'?[content]:[],querySelector:()=>null};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',contentDocument:driver};
const document={readyState:'complete',querySelectorAll:selector=>
  selector==='iframe[title="container"]'||selector==='iframe'?[container]:[],querySelector:()=>null};
const context={document,location:{href:data.frame}};
const run=action=>vm.runInNewContext(data.script+'('+JSON.stringify({
  frame:data.frame,heading:'Generating adversarial examples',lesson:data.lesson,
  continue_label:'DO IT YOURSELF EXERCISE EIGHT',action})+')',context);
const before=run('inspect');
const opened=data.extra||data.extraInput?null:run('expand');
const read=data.extra||data.extraInput?null:run('scroll');
top=8000;
const next=data.extra||data.extraInput?null:run('continue');
const after=data.extra||data.extraInput?null:run('inspect');
process.stdout.write(JSON.stringify({before,opened,read,next,after}));
'''
        for case in ({"extra": False, "extraInput": False},
                     {"extra": True, "extraInput": False},
                     {"extra": False, "extraInput": True}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.EXERCISE_JS,
                    "frame": flow.GENERATING_EXAMPLES_FRAME,
                    "lesson": flow.GENERATING_EXAMPLES_LESSON}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                if case["extra"] or case["extraInput"]:
                    self.assertIn("unhandled interactive control", value["before"]["error"])
                else:
                    self.assertEqual(value["before"]["stage"], "reading")
                    self.assertEqual(value["opened"]["clicked"], "Remarks")
                    self.assertGreater(value["read"]["top"], 0)
                    self.assertEqual(value["next"]["clicked"],
                                     "DO IT YOURSELF EXERCISE EIGHT")
                    self.assertEqual(value["after"]["stage"], "continued")

    def test_poisoning_script_checks_reading_transition_and_rejects_new_controls(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let stage='reading',top=0;
const base=data.frame.replace(/index\.html$/,'');
const button=(text,extra={})=>({innerText:text,getClientRects:()=>[1],...extra});
const chrome=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']
  .map(text=>button(text));
const cont=button('DO IT YOURSELF EXERCISE NINE',{click:()=>{stage='continued';top=0}});
const extra=button('Submit answer');
const search={type:'search',disabled:true};
const form={matches:selector=>selector==='form.nav-sidebar-search--disabled',
  querySelectorAll:selector=>selector==='input'?[search]:[]};
const wrap={get innerText(){return 'Poisoning Attacks RandomForestClassifier RocCurveDisplay '+
  'A'.repeat(2600)+(stage==='continued'?
    ' In this ninth do it yourself exercise there are no solutions to this exercise.':'')},
  clientHeight:550,get scrollHeight(){return (stage==='continued'?2417:2341)+550},
  get scrollTop(){return top},set scrollTop(value){top=value}};
const page={URL:base+'scorm-content/scormcontent/index.html#/lessons/'+data.lesson,
  readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
  querySelectorAll:selector=>({
    'iframe':[],'div.page-wrap':[wrap],
    'button.continue-btn':stage==='reading'?[cont]:[],
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close':chrome,
    'button,[role="button"]':[...chrome,...(stage==='reading'?[cont]:[]),
      ...(data.extra?[extra]:[])],
    'input':[search],'form':[form]
  }[selector]||[]),
  querySelector:selector=>selector==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:
    selector==='input[type="search"][disabled]'?search:null};
const content={title:'Poisoning attacks',contentDocument:page};
const driver={readyState:'complete',querySelectorAll:selector=>
  selector==='iframe#content-frame'?[content]:[],querySelector:()=>null};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',contentDocument:driver};
const document={readyState:'complete',querySelectorAll:selector=>
  selector==='iframe[title="container"]'||selector==='iframe'?[container]:[],querySelector:()=>null};
const context={document,location:{href:data.frame}};
const run=action=>vm.runInNewContext(data.script+'('+JSON.stringify({
  frame:data.frame,heading:'Poisoning attacks',lesson:data.lesson,
  continue_label:'DO IT YOURSELF EXERCISE NINE',action})+')',context);
const before=run('inspect');
const read=data.extra?null:run('scroll');
top=2341;
const next=data.extra?null:run('continue');
const after=data.extra?null:run('inspect');
process.stdout.write(JSON.stringify({before,read,next,after}));
'''
        for extra in (False, True):
            with self.subTest(extra=extra):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    "extra": extra, "script": flow.POISONING_JS,
                    "frame": flow.POISONING_FRAME, "lesson": flow.POISONING_LESSON}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                if extra:
                    self.assertIn("unhandled interactive control", value["before"]["error"])
                else:
                    self.assertEqual(value["before"]["stage"], "reading")
                    self.assertGreater(value["read"]["top"], 0)
                    self.assertEqual(value["next"]["clicked"],
                                     "DO IT YOURSELF EXERCISE NINE")
                    self.assertEqual(value["after"]["stage"], "continued")

    def test_summary_script_permits_only_four_exact_self_check_boxes(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
const outcomes=[
  'Describe security risks in real-world AI applications.',
  'Explore AI-specific threats such as model poisoning, adversarial attacks, and backdoor exploits.',
  'Understand risks associated with model leakage, data privacy breaches, and AI-powered surveillance.',
  'Evaluate challenges in AI model alignment, censorship, and ethical concerns.'
];
if(data.changedLabel)outcomes[1]='A different learning outcome.';
if(data.extraBox)outcomes.push('A fifth outcome.');
let boxClicks=0,top=0;
const boxes=outcomes.map(text=>{
  const label={querySelector:s=>s==='.block-list__content'?{innerText:text}:null};
  return {type:'checkbox',disabled:false,required:false,
    closest:s=>s==='label.block-list__option'?label:null,
    click:()=>{boxClicks++}};
});
const list={querySelectorAll:s=>s==='input.block-list__checkbox-input'?boxes:[]};
const button=text=>({innerText:text,getClientRects:()=>[1]});
const chrome=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']
  .map(button);
const submit=button('Submit answer');
const search={type:'search',disabled:true};
const searchForm={matches:s=>s==='form.nav-sidebar-search--disabled',
  querySelectorAll:s=>s==='input'?[search]:[]};
const wrap={innerText:'Topic complete LESSON 1 OF 1 Congratulations, you have now completed this topic! '
  +'You are now able to: '+outcomes.join(' ')+' References '+'A'.repeat(4800),
  clientHeight:500,scrollHeight:2800,
  get scrollTop(){return top},set scrollTop(v){top=v}};
const page={URL:base+'scorm-content/scormcontent/index.html#/lessons/'+
  (data.wrongLesson?'another-lesson':data.lesson),
  readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
  querySelectorAll:s=>({
    'iframe':[],'div.page-wrap':[wrap],
    'div.block-list--checkboxes':[list],
    'input.block-list__checkbox-input':boxes,
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close':chrome,
    'button,[role="button"]':[...chrome,...(data.submit?[submit]:[])],
    'input':[search,...boxes],'form':[searchForm]
  }[s]||[]),
  querySelector:s=>s==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:
    s==='input[type="search"][disabled]'?search:null};
const content={title:'Topic 3: Summary',contentDocument:page};
const auxiliary=[
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}
];
if(data.badAux)auxiliary[0].src=base+'scorm-content/scormdriver/unexpected.html';
const driver={readyState:'complete',querySelectorAll:s=>
  s==='iframe#content-frame'?[content]:s==='iframe'?[content,...auxiliary]:[],
  querySelector:()=>null};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',contentDocument:driver};
const document={readyState:'complete',querySelectorAll:s=>
  s==='iframe[title="container"]'||s==='iframe'?[container]:[],querySelector:()=>null};
const context={document,location:{href:data.frame}};
const run=action=>vm.runInNewContext(data.script+'('+JSON.stringify({
  frame:data.frame,heading:'Summary',lesson:data.lesson,action})+')',context);
const before=run('inspect'),read=run('scroll');
process.stdout.write(JSON.stringify({before,read,top,boxClicks}));
'''
        for case in ({"extraBox": False, "changedLabel": False, "submit": False,
                      "wrongLesson": False, "badAux": False},
                     {"extraBox": True}, {"changedLabel": True},
                     {"submit": True}, {"wrongLesson": True}, {"badAux": True}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.SUMMARY_JS,
                    "frame": flow.SUMMARY_FRAME, "lesson": flow.SUMMARY_LESSON}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual(value["boxClicks"], 0)
                if any(case.values()):
                    self.assertIn("error", value["before"])
                    self.assertEqual(value["top"], 0)
                else:
                    self.assertEqual(value["before"]["stage"], "reading")
                    self.assertEqual(value["before"]["checkbox_count"], 4)
                    self.assertGreater(value["top"], 0)
                    self.assertNotIn("error", value["read"])

    def test_overview4_outer_script_rejects_wrong_path_frame_and_title(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let clicks=0;
const mark={innerText:'Mark as completed',disabled:false,
  getAttribute:()=>null,closest:()=>null,scrollIntoView:()=>{},click:()=>{clicks++}};
const frame={title:data.wrongTitle?'Topic 3: Summary':'Topic 4: Overview',
  src:data.wrongFrame?data.frame.replace('b4ebb914','wrong-asset'):data.frame};
const root={querySelectorAll:s=>s==='iframe[data-testid="widget-iframe"]'?[frame]:
  s==='button[data-testid="mark-complete"]'||s==='button,[role="button"]'?[mark]:[]};
const document={readyState:'complete',querySelectorAll:s=>s==='#main-container'?[root]:
  s==='h1'?[{textContent:'Overview'},{textContent:'Overview'}]:[]};
const location={href:data.wrongPath?data.url.replace('/YFamB/','/wrong/'):data.url};
const request={path:data.path,heading:'Overview',frame:data.frame,
  frame_title:'Topic 4: Overview',action:'mark',inner_verified:true};
const result=vm.runInNewContext(data.script+'('+JSON.stringify(request)+')',
  {document,location,URL});
process.stdout.write(JSON.stringify({result,clicks}));
'''
        for case in ({}, {"wrongPath": True}, {"wrongFrame": True},
                     {"wrongTitle": True}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.OUTER_JS, "path": flow.OVERVIEW4_PATH,
                    "url": "https://www.coursera.org" + flow.OVERVIEW4_PATH,
                    "frame": flow.OVERVIEW4_FRAME}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual(value["clicks"], 0 if case else 1)
                self.assertEqual("error" in value["result"], bool(case))

    def test_introduction4_outer_script_rejects_wrong_path_frame_and_title(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let clicks=0;
const mark={innerText:'Mark as completed',disabled:false,
  getAttribute:()=>null,closest:()=>null,scrollIntoView:()=>{},click:()=>{clicks++}};
const frame={title:data.wrongTitle?'Topic 3: Introduction':'Topic 4: Introduction',
  src:data.wrongFrame?data.frame.replace('98a1815d','wrong-asset'):data.frame};
const root={querySelectorAll:s=>s==='iframe[data-testid="widget-iframe"]'?[frame]:
  s==='button[data-testid="mark-complete"]'||s==='button,[role="button"]'?[mark]:[]};
const document={readyState:'complete',querySelectorAll:s=>s==='#main-container'?[root]:
  s==='h1'?[{textContent:'Introduction'},{textContent:'Introduction'}]:[]};
const location={href:data.wrongPath?data.url.replace('/xBnh8/','/wrong/'):data.url};
const request={path:data.path,heading:'Introduction',frame:data.frame,
  frame_title:'Topic 4: Introduction',action:'mark',inner_verified:true};
const result=vm.runInNewContext(data.script+'('+JSON.stringify(request)+')',
  {document,location,URL});
process.stdout.write(JSON.stringify({result,clicks}));
'''
        for case in ({}, {"wrongPath": True}, {"wrongFrame": True},
                     {"wrongTitle": True}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.OUTER_JS, "path": INTRODUCTION4_PATH,
                    "url": "https://www.coursera.org" + INTRODUCTION4_PATH,
                    "frame": INTRODUCTION4_FRAME}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual(value["clicks"], 0 if case else 1)
                self.assertEqual("error" in value["result"], bool(case))

    def test_defence4_outer_script_rejects_wrong_path_frame_and_title(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let clicks=0;
const mark={innerText:'Mark as completed',disabled:false,
  getAttribute:()=>null,closest:()=>null,scrollIntoView:()=>{},click:()=>{clicks++}};
const frame={title:data.wrongTitle?'Another lesson':'Defence techniques and strategies',
  src:data.wrongFrame?data.frame.replace('75939444','wrong-asset'):data.frame};
const root={querySelectorAll:s=>s==='iframe[data-testid="widget-iframe"]'?[frame]:
  s==='button[data-testid="mark-complete"]'||s==='button,[role="button"]'?[mark]:[]};
const document={readyState:'complete',querySelectorAll:s=>s==='#main-container'?[root]:
  s==='h1'?[{textContent:'Defence techniques and strategies'},
            {textContent:'Defence techniques and strategies'}]:[]};
const location={href:data.wrongPath?data.url.replace('/gTEoB/','/wrong/'):data.url};
const request={path:data.path,heading:'Defence techniques and strategies',
  frame:data.frame,frame_title:'Defence techniques and strategies',
  action:'mark',inner_verified:true};
const result=vm.runInNewContext(data.script+'('+JSON.stringify(request)+')',
  {document,location,URL});
process.stdout.write(JSON.stringify({result,clicks}));
'''
        for case in ({}, {"wrongPath": True}, {"wrongFrame": True},
                     {"wrongTitle": True}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.OUTER_JS, "path": DEFENCE4_PATH,
                    "url": "https://www.coursera.org" + DEFENCE4_PATH,
                    "frame": DEFENCE4_FRAME}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual(value["clicks"], 0 if case else 1)
                self.assertEqual("error" in value["result"], bool(case))

    def test_genai4_outer_script_rejects_wrong_path_frame_and_title(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
let clicks=0;
const mark={innerText:'Mark as completed',disabled:false,
  getAttribute:()=>null,closest:()=>null,scrollIntoView:()=>{},click:()=>{clicks++}};
const frame={title:data.wrongTitle?'Another lesson':'Defences for attacks on GenAI models',
  src:data.wrongFrame?data.frame.replace('fb3d4742','wrong-asset'):data.frame};
const root={querySelectorAll:s=>s==='iframe[data-testid="widget-iframe"]'?[frame]:
  s==='button[data-testid="mark-complete"]'||s==='button,[role="button"]'?[mark]:[]};
const document={readyState:'complete',querySelectorAll:s=>s==='#main-container'?[root]:
  s==='h1'?[{textContent:'Defences for attacks on GenAI models'},
            {textContent:'Defences for attacks on GenAI models'}]:[]};
const location={href:data.wrongPath?data.url.replace('/6uBZN/','/wrong/'):data.url};
const request={path:data.path,heading:'Defences for attacks on GenAI models',
  frame:data.frame,frame_title:'Defences for attacks on GenAI models',
  action:'mark',inner_verified:true};
const result=vm.runInNewContext(data.script+'('+JSON.stringify(request)+')',
  {document,location,URL});
process.stdout.write(JSON.stringify({result,clicks}));
'''
        for case in ({}, {"wrongPath": True}, {"wrongFrame": True},
                     {"wrongTitle": True}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.OUTER_JS, "path": GENAI4_PATH,
                    "url": "https://www.coursera.org" + GENAI4_PATH,
                    "frame": GENAI4_FRAME}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual(value["clicks"], 0 if case else 1)
                self.assertEqual("error" in value["result"], bool(case))

    def test_overview4_inner_script_rejects_changed_structure_and_controls(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
const outcomes=[
  'Recommend guidelines and common defences against AI security attacks.',
  'Apply AI controls to mitigate security risks.',
  'Contrast the trade-offs between security, utility, and robustness of AI models.',
  'Plan processes to conduct AI security testing and benchmarking.'
];
if(data.changedLabel)outcomes[1]='A different learning outcome.';
if(data.extraBox)outcomes.push('A fifth outcome.');
let boxClicks=0,top=0;
const boxes=outcomes.map(text=>{
  const label={querySelector:s=>s==='.block-list__content'?{innerText:text}:null};
  return {type:'checkbox',disabled:false,required:false,
    closest:s=>s==='label.block-list__option'?label:null,
    click:()=>{boxClicks++}};
});
const list={querySelectorAll:s=>s==='input.block-list__checkbox-input'?boxes:[]};
const button=text=>({innerText:text,getClientRects:()=>[1]});
const chrome=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']
  .map(button);
const submit=button('Submit answer');
const search={type:'search',disabled:true};
const searchForm={matches:s=>s==='form.nav-sidebar-search--disabled',
  querySelectorAll:s=>s==='input'?[search]:[]};
const wrap={innerText:'Topic overview LESSON 1 OF 1 Testing AI security and developing robust AI controls '+
  'This topic describes methods for testing AI security. '+outcomes.join(' ')+'A'.repeat(1000),
  clientHeight:550,scrollHeight:data.shortScroll?550:895,
  get scrollTop(){return top},set scrollTop(v){top=v}};
const page={URL:base+'scorm-content/scormcontent/index.html#/lessons/'+
  (data.wrongLesson?'another-lesson':data.lesson),
  readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
  querySelectorAll:s=>({
    'iframe':[],'div.page-wrap':[wrap],
    'div.block-list--checkboxes':[list],
    'input.block-list__checkbox-input':boxes,
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close':chrome,
    'button,[role="button"]':[...chrome,...(data.submit?[submit]:[])],
    'input':[search,...boxes],'form':[searchForm]
  }[s]||[]),
  querySelector:s=>s==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:
    s==='input[type="search"][disabled]'?search:null};
const content={title:data.wrongTitle?'Topic 3: Summary':'Topic 4: Overview',contentDocument:page};
const auxiliary=[
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
  {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}
];
const driver={readyState:'complete',querySelectorAll:s=>
  s==='iframe#content-frame'?[content]:s==='iframe'?[content,...auxiliary]:[],
  querySelector:()=>null};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',contentDocument:driver};
const document={readyState:'complete',querySelectorAll:s=>
  s==='iframe[title="container"]'||s==='iframe'?[container]:[],querySelector:()=>null};
const context={document,location:{href:data.wrongFrame?
  data.frame.replace('b4ebb914','wrong-asset'):data.frame}};
const run=action=>vm.runInNewContext(data.script+'('+JSON.stringify({
  frame:data.frame,heading:'Overview',lesson:data.lesson,action})+')',context);
const before=run('inspect'),read=run('scroll');
process.stdout.write(JSON.stringify({before,read,top,boxClicks}));
'''
        for case in ({}, {"extraBox": True}, {"changedLabel": True},
                     {"submit": True}, {"wrongLesson": True},
                     {"wrongTitle": True}, {"wrongFrame": True},
                     {"shortScroll": True}):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.OVERVIEW4_JS,
                    "frame": flow.OVERVIEW4_FRAME, "lesson": flow.OVERVIEW4_LESSON}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                self.assertEqual(value["boxClicks"], 0)
                if case:
                    self.assertIn("error", value["before"])
                    self.assertEqual(value["top"], 0)
                else:
                    self.assertEqual(value["before"]["stage"], "reading")
                    self.assertEqual(value["before"]["checkbox_count"], 4)
                    self.assertGreater(value["top"], 0)
                    self.assertNotIn("error", value["read"])

    def test_introduction4_inner_script_checks_both_stages_and_flashcard_states(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
let stage=data.startContinued?'continued':'reading',top=0,clicks=0;
const pairs=[
 ['Unauthorised manipulation','To impact the predictions made by the model.'],
 ['Privacy breaches','To retrieve the data it was trained on.'],
 ['Reverse engineering.','To steal the model']
];
if(data.wrongCardText)pairs[1][1]='An unexpected reverse side';
if(data.extraCard)pairs.push(['Extra card','Extra answer']);
const flipped=pairs.map((_,i)=>Boolean(data.partlyFlipped&&i===0));
const button=(text,extra={})=>({innerText:text,textContent:text,type:'button',
  getClientRects:()=>[1],getAttribute:()=>null,parentElement:null,
  scrollIntoView:()=>{},hidden:false,inert:false,...extra});
const chrome=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']
  .map(button);
const cont=button('CONTINUE',{click:()=>{stage='continued';top=0;clicks++}});
const zoom=button('');const unzoom=button('');
const extra=button('Submit answer');
const unloadedImage={getAttribute:n=>n==='src'?(
  data.wrongMedia?'assets/other.png':'assets/S4AI%20Topic%20Intros_Topic%204.png'):null};
const media={querySelectorAll:s=>s==='[data-rmiz-content="not-found"] img'?
  [unloadedImage]:[],
  querySelector:s=>s==='[data-rmiz-content="not-found"] img'?unloadedImage:null};
const cards=pairs.map((pair,i)=>{
 const front={innerText:'Click to flip '+pair[0],
   classList:{contains:c=>c==='flashcard-side--front'||
     (c==='flashcard-side--flipped'&&flipped[i])}};
 const back={innerText:'Click to flip '+pair[1],
   classList:{contains:c=>c==='flashcard-side--back'||
     (c==='flashcard-side--flipped'&&flipped[i])}};
 const frontButton=button('',{id:'card-'+i+'-front-flip-btn',
   getClientRects:()=>data.hiddenFlip&&i===0?[]:[1],
   click:()=>{flipped[i]=true;clicks++}});
 const backButton=button('',{id:'card-'+i+'-back-flip-btn',
   click:()=>{flipped[i]=false;clicks++}});
 Object.defineProperty(frontButton,'tabIndex',{get:()=>flipped[i]?-1:0});
 Object.defineProperty(backButton,'tabIndex',{get:()=>flipped[i]?0:-1});
 const card={getAttribute:n=>n==='role'?'listitem':n==='aria-hidden'?'false':null,
   getClientRects:()=>data.hiddenCard&&i===0?[]:[1],
   querySelectorAll:s=>s==='.flashcard-side'?[front,back]:
     s==='button.flashcard-side-flip__btn'?[frontButton,backButton]:[],
   parentElement:null,hidden:false,inert:false};
 frontButton.parentElement=card;backButton.parentElement=card;
 frontButton.closest=s=>s==='li.flashcard'?card:null;
 backButton.closest=s=>s==='li.flashcard'?card:null;
 return card;
});
const search={type:'search',disabled:true,
  matches:s=>s==='input[type="search"][disabled]'};
const form={matches:s=>s==='form.nav-sidebar-search--disabled',
  querySelectorAll:s=>s==='input'?[search]:[]};
const wrap={get innerText(){return 'Introduction LESSON 1 OF 1 '+
  'The security, robustness, and privacy guarantees. '+
  'Security controls are countermeasures or safeguards. '+
  'This topic consists of the following subtopics: AI Security Testing and Benchmarking. '+
  'A'.repeat(2050)+(stage==='continued'?
    ' '+(data.withContinuedStatus?'Continued ':'')+'Know thine enemy. Flip each card to discover the key reasons for adversarial attacks on an AI system. '+
    'Attackers can also be interested in stealing the model itself. '+'B'.repeat(2500):'')},
  clientHeight:550,get scrollHeight(){return data.shortScroll?550:
    (stage==='continued'?4809:2752)},
  get scrollTop(){return top},set scrollTop(v){top=v}};
const page={URL:base+'scorm-content/scormcontent/index.html#/lessons/'+
  (data.wrongLesson?'another-lesson':data.lesson),readyState:'complete',
  body:{innerText:'LESSON 1 OF 1'},
  defaultView:{getComputedStyle:node=>({display:
    (data.cssHiddenCard&&node===cards[0])||
    (data.cssHiddenFlip&&node===cards[0].querySelectorAll('button.flashcard-side-flip__btn')[0])
      ?'none':'block',visibility:'visible'})},
  querySelectorAll:s=>({
    'iframe':[],'div.page-wrap':[wrap],
    '[data-rmiz]':data.noZoom&&!data.missingMedia?[media]:[],
    'button.continue-btn':stage==='reading'?[cont]:[],
    'li.flashcard':stage==='continued'?cards:[],
    'button[data-rmiz-btn-zoom][aria-label="Zoom image"]':data.noZoom?[]:[zoom],
    'button[data-rmiz-btn-unzoom][aria-label="Unzoom image"]':data.noZoom?[]:[unzoom],
    'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close':chrome,
    'button,[role="button"]':[...chrome,...(data.noZoom?[]:[zoom,unzoom]),
      ...(stage==='reading'?[cont]:[]),
      ...(stage==='continued'?cards.flatMap(c=>c.querySelectorAll('button.flashcard-side-flip__btn')):[]),
      ...(data.extraButton?[extra]:[])],
    'input':data.extraInput?[search,{}]:[search],'form':[form]
  }[s]||[]),
  querySelector:s=>s==='.nav-sidebar-header__progress-text'?{innerText:'0% COMPLETE'}:null};
const content={title:data.wrongTitle?'Topic 3: Introduction':'Topic 4: Introduction',
  contentDocument:page};
const auxiliary=[
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}
];
const driver={readyState:'complete',querySelectorAll:s=>
  s==='iframe#content-frame'?[content]:s==='iframe'?[content,...auxiliary]:[],
  querySelector:()=>null};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',contentDocument:driver};
const document={readyState:'complete',querySelectorAll:s=>
  s==='iframe[title="container"]'||s==='iframe'?[container]:[],querySelector:()=>null};
const context={document,location:{href:data.wrongFrame?
  data.frame.replace('98a1815d','wrong-asset'):data.frame}};
const run=action=>vm.runInNewContext(data.script+'('+JSON.stringify({
  frame:data.frame,heading:'Introduction',lesson:data.lesson,action})+')',context);
const before=run('inspect');
let premature=null,continued=null,after=null,flip=null,post=null;
if(!before.error){
  if(stage==='reading'){
    premature=run('continue');top=2202;continued=run('continue');after=run('inspect');
  }
  if(stage==='continued'){
    flip=run('flip');post=run('inspect');
  }
}
process.stdout.write(JSON.stringify({before,premature,continued,after,flip,post,top,clicks}));
'''
        valid_cases = ({}, {"startContinued": True},
                       {"startContinued": True, "noZoom": True},
                       {"startContinued": True, "withContinuedStatus": True},
                       {"startContinued": True, "partlyFlipped": True})
        bad_cases = ({"wrongFrame": True}, {"wrongLesson": True},
                     {"wrongTitle": True}, {"shortScroll": True},
                     {"noZoom": True},
                     {"startContinued": True, "noZoom": True, "missingMedia": True},
                     {"startContinued": True, "noZoom": True, "wrongMedia": True},
                     {"extraButton": True}, {"extraInput": True},
                     {"startContinued": True, "wrongCardText": True},
                     {"startContinued": True, "extraCard": True},
                     {"startContinued": True, "hiddenCard": True},
                     {"startContinued": True, "cssHiddenCard": True})
        hidden_button_cases = ({"startContinued": True, "hiddenFlip": True},
                               {"startContinued": True, "cssHiddenFlip": True})
        for case in (*valid_cases, *bad_cases, *hidden_button_cases):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.INTRODUCTION4_JS,
                    "frame": INTRODUCTION4_FRAME, "lesson": INTRODUCTION4_LESSON}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                if case in bad_cases:
                    self.assertIn("error", value["before"])
                    self.assertEqual(value["clicks"], 0)
                elif case in hidden_button_cases:
                    self.assertEqual(value["before"]["stage"], "continued")
                    self.assertIn("error", value["flip"])
                    self.assertEqual(value["clicks"], 0)
                elif case.get("startContinued"):
                    self.assertEqual(value["before"]["stage"], "continued")
                    self.assertEqual(value["before"]["card_count"], 3)
                    self.assertEqual(value["flip"]["clicked"], "flip")
                    self.assertIn("flipped_count", value["post"], value)
                    self.assertEqual(value["post"]["flipped_count"],
                                     2 if case.get("partlyFlipped") else 1)
                else:
                    self.assertEqual(value["before"]["stage"], "reading")
                    self.assertIn("error", value["premature"])
                    self.assertEqual(value["continued"]["clicked"], "CONTINUE")
                    self.assertEqual(value["after"]["stage"], "continued")
                    self.assertIn("flipped_count", value["post"], value)
                    self.assertEqual(value["post"]["flipped_count"], 1)

    def test_defence4_inner_script_checks_reading_activities_and_scenario(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
const stages=['intro','evasion','poisoning','inversion','scenario_intro','scenario','terminal'];
const stage=data.stage.startsWith('choice')||data.stage==='feedback'?'scenario':data.stage;
const rank=stages.indexOf(stage);
const markers=['Defences for evasion attacks','Defences for poisoning attacks',
 'Defences for inversion attacks','Defences for model extraction attacks'];
const maxByStage={intro:0,evasion:5621,poisoning:7604,inversion:11400,
 scenario_intro:13000,scenario:13000,terminal:13000};
let top=0,clicks=[],expanded=[false,false,false],flipped=[false,false,false];
const button=(text,extra={})=>({innerText:text,textContent:text,type:'button',
 getClientRects:()=>[1],getAttribute:()=>null,scrollIntoView:()=>{},
 parentElement:null,hidden:false,inert:false,click:()=>clicks.push(text),...extra});
const nav=['SKIP TO LESSON','Open navigation menu','Open search menu','Close search menu']
 .map(text=>button(text));
const continueButton=button(stage==='scenario_intro'?'Bring on the scenario!':'CONTINUE');
const accordionNames=['Data sanitisation','Outlier detection','Robust optimisation'];
const panelStarts=['Filtering and cleaning the training data',
 'Detecting and excluding outliers in the training data',
 'Similar to adversarial training, with this defence technique'];
const accordions=rank>=2?accordionNames.map((name,index)=>button(
 data.wrongAccordion&&index===1?'Unknown defence':name,{
 getClientRects:()=>data.hiddenAccordion&&index===0?[]:[1],
 getAttribute:key=>key==='aria-controls'?'panel-'+index:
   key==='aria-expanded'?(expanded[index]?'true':'false'):null,
 click:()=>{expanded[index]=true;clicks.push('expand-'+index)}})):[];
if(data.extraAccordion)accordions.push(button('Unknown accordion'));
const cardPairs=[
 ['Differential privacy','A mathematical privacy notion that has been widely used'],
 ['Homomorphic encryption','Allows computations and predictions to be performed'],
 ['Secure multi-party computation (SMC)','Enables multiple parties to collaboratively']];
const cards=rank>=3?cardPairs.map((pair,index)=>{
 const front={innerText:'Click to flip '+pair[0],classList:{contains:key=>
   key==='flashcard-side--flipped'&&flipped[index]}};
 const back={innerText:'Click to flip '+(data.wrongCard&&index===1?'Unknown back':pair[1]),
   classList:{contains:key=>key==='flashcard-side--flipped'&&flipped[index]}};
 const frontButton=button('',{id:'card-'+index+'-front-flip-btn',
   getClientRects:()=>data.hiddenFlip&&index===0?[]:[1],
   click:()=>{flipped[index]=true;clicks.push('flip-'+index)}});
 const backButton=button('',{id:'card-'+index+'-back-flip-btn',
   click:()=>{flipped[index]=false;clicks.push('back-'+index)}});
 Object.defineProperty(frontButton,'tabIndex',{get:()=>flipped[index]?-1:0});
 Object.defineProperty(backButton,'tabIndex',{get:()=>flipped[index]?0:-1});
 const card={getClientRects:()=>data.hiddenCard&&index===0?[]:[1],
   getAttribute:key=>key==='role'?'listitem':key==='aria-hidden'?'false':null,
   querySelector:key=>key==='.flashcard-side--front'?front:
     key==='.flashcard-side--back'?back:null,
   querySelectorAll:key=>key==='button.flashcard-side-flip__btn'?
     [frontButton,backButton]:[],parentElement:null,hidden:false,inert:false};
 frontButton.parentElement=card;backButton.parentElement=card;
 frontButton.closest=key=>key==='li.flashcard'?card:null;
 backButton.closest=key=>key==='li.flashcard'?card:null;
 return card;
}):[];
if(data.extraCard)cards.push({});
const prompts=[
 'Can I run a few questions by you?',
 'slightly tweaking a query in subtle ways',
 'prevent membership inference attacks on this system',
 'How can we prevent someone from stealing our AI system',
 'Are you happy with the advice you\'ve given or would you like to revisit the scenario?'];
const options=[['Sure thing'],
 ['Adversarial training','Differential privacy','Implementing access controls'],
 ['Access control','Differential privacy','Defensive distillation'],
 ['Use watermarking','Implement access controls','Encrypt training data'],
 ["Let's leave it there, we can pick this up again later.",
  'I want to do the scenario again.']];
const choiceIndex=Number(data.choiceIndex||0);
const scenarioChoices=data.stage.startsWith('choice')?
 options[choiceIndex].map((text,index)=>button((index+1)+' '+
  (data.wrongOption&&index===0?'Unexpected option':text),{
   click:()=>clicks.push('choice-'+index)})):[];
const scenarioContinue=data.stage==='feedback'?button('CONTINUE',{
 matches:key=>key==='button.scenario-block__text__continue,button.scenario-block__dialogue__button',
 click:()=>clicks.push('scenario-continue')}):null;
const startOver=stage==='terminal'?button('START OVER',{
 matches:key=>key==='button.scenario-block__text__end',
 click:()=>clicks.push('start-over')}):null;
const scenarioButtons=stage==='scenario'?data.transient?[]:data.stage==='feedback'?
 [scenarioContinue]:scenarioChoices:stage==='terminal'?[startOver]:
 data.extraScenarioButton||data.previewUnknownButton?
 [button('Unexpected scenario action')]:[];
const scenario=rank>=4?{
 classList:{contains:key=>key==='scenario-block--initiated'&&rank>=5},
 innerText:stage==='terminal'?'Scenario End I feel much more confident':
  data.unknownFeedback?'AI Security scenario 1 Old known text. Unknown current feedback. CONTINUE':
  data.stage==='feedback'?data.scenarioOpening?
   'AI Security scenario 1\nHow to improve AI defence against potential attacks on the system at XYZCo?\nCONTINUE':
   data.stableDialogue?
   "Hi I'm Chantelle Patel from the Business intelligence department of XYZCo. CONTINUE":
   "AI Security scenario 1 Earlier text. Thanks, let's start. CONTINUE":
   'AI Security scenario 1 Hi I\'m Chantelle Patel '+prompts[choiceIndex],
 querySelectorAll:key=>key==='button,[role="button"]'?scenarioButtons:
   key==='button.scenario-block__response'?scenarioChoices:[],
 querySelector:key=>key==='.scenario-block__dialogue'?
   {innerText:prompts[choiceIndex]}:null}:null;
const textBase='Defence techniques and strategies LESSON 1 OF 1 '+
 'In the previous topics, we explored different types of adversarial AI attacks. '+
 (data.badOrder?markers[1]+' ':markers.slice(0,Math.min(rank,4)).join(' '))+
 (rank>=4?' This activity helped identify effective AI security controls. ':' ');
const padding={intro:300,evasion:5700,poisoning:7000,inversion:11000,
 scenario_intro:13000,scenario:13000,terminal:13000}[stage];
const wrap={innerText:textBase+'A'.repeat(padding),clientHeight:550,
 scrollHeight:(data.shortScroll?200:maxByStage[stage])+550,
 get scrollTop(){return top},set scrollTop(value){top=value}};
const search={type:'search',disabled:true,
 matches:key=>key==='input[type="search"][disabled]'};
const form={matches:key=>key==='form.nav-sidebar-search--disabled',
 querySelectorAll:key=>key==='input'?[search]:[]};
const allButtons=[...nav,...accordions,
 ...cards.flatMap(card=>card.querySelectorAll?.('button.flashcard-side-flip__btn')||[]),
 ...(rank>=5||data.previewTransition||data.previewUnknownButton?[]:[continueButton]),
 ...scenarioButtons,
 ...(data.extraButton?[button('Submit answer')]:[])];
const page={URL:base+'scorm-content/scormcontent/index.html#/lessons/'+
 (data.wrongLesson?'another-lesson':data.lesson),readyState:'complete',
 body:{innerText:'LESSON 1 OF 1'},
 defaultView:{getComputedStyle:node=>({display:
   data.cssHiddenCard&&node===cards[0]?'none':'block',visibility:'visible'})},
 getElementById:key=>key.startsWith('panel-')?{
   textContent:data.wrongPanel&&key==='panel-1'?'Wrong explanation':
    panelStarts[Number(key.slice(6))]+' longer explanation'}:null,
 querySelectorAll:key=>({
   'iframe':[],'div.page-wrap':[wrap],
   'button.blocks-accordion__header':accordions,
   'li.flashcard':cards,
   'button.continue-btn':rank>=5||data.previewTransition||data.previewUnknownButton?
     []:[continueButton],
   'button[data-rmiz-btn-zoom]':[],
   'button[data-rmiz-btn-unzoom]':[],
   'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close':nav,
   'button,[role="button"]':allButtons,
   'input':data.extraInput?[search,{}]:[search],
   'form':[form]
 }[key]||[]),
 querySelector:key=>key==='.nav-sidebar-header__progress-text'?
   {innerText:'0% COMPLETE'}:key==='.scenario-block'?scenario:null};
const content={title:data.wrongTitle?'Another lesson':'Defence techniques and strategies',
 contentDocument:page};
const auxiliary=[
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}];
const driver={readyState:'complete',querySelectorAll:key=>
 key==='iframe#content-frame'?[content]:key==='iframe'?[content,...auxiliary]:[],
 querySelector:()=>null};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',contentDocument:driver};
const document={readyState:'complete',querySelectorAll:key=>
 key==='iframe[title="container"]'||key==='iframe'?[container]:[],querySelector:()=>null};
const context={document,location:{href:data.wrongFrame?
 data.frame.replace('75939444','wrong-asset'):data.frame}};
const run=action=>vm.runInNewContext(data.script+'('+JSON.stringify({
 frame:data.frame,heading:'Defence techniques and strategies',
 lesson:data.lesson,action})+')',context);
const before=run('inspect');let premature=null,actions=[],done=null;
if(!before.error&&!before.pending){
 if(['evasion','poisoning','inversion','scenario_intro'].includes(stage))
   premature=run('continue');
 if(['poisoning','inversion'].includes(stage))
   for(let n=0;n<3;n++)actions.push(run('expand'));
 if(stage==='inversion')for(let n=0;n<3;n++)actions.push(run('flip'));
 if(['intro','evasion','poisoning','inversion','scenario_intro'].includes(stage)){
   top=maxByStage[stage];done=run('continue');
 }else if(stage==='scenario')done=run(data.stage==='feedback'?
   'scenario_continue':'scenario_choice');
 else done=run('scenario_choice');
}
process.stdout.write(JSON.stringify({before,premature,actions,done,clicks,top}));
'''
        valid = ({"stage": "intro"}, {"stage": "evasion"},
                 {"stage": "poisoning"}, {"stage": "inversion"},
                 {"stage": "scenario_intro"},
                 *({"stage": f"choice{index}", "choiceIndex": index}
                   for index in range(5)),
                 {"stage": "feedback"}, {"stage": "feedback", "stableDialogue": True},
                 {"stage": "feedback", "scenarioOpening": True},
                 {"stage": "terminal"})
        invalid = ({"stage": "evasion", "wrongFrame": True},
                   {"stage": "evasion", "wrongLesson": True},
                   {"stage": "evasion", "wrongTitle": True},
                   {"stage": "evasion", "badOrder": True},
                   {"stage": "evasion", "shortScroll": True},
                   {"stage": "poisoning", "wrongAccordion": True},
                   {"stage": "poisoning", "wrongPanel": True},
                   {"stage": "poisoning", "extraAccordion": True},
                   {"stage": "poisoning", "hiddenAccordion": True},
                   {"stage": "inversion", "wrongCard": True},
                   {"stage": "inversion", "extraCard": True},
                   {"stage": "inversion", "hiddenCard": True},
                   {"stage": "inversion", "cssHiddenCard": True},
                   {"stage": "choice1", "choiceIndex": 1, "wrongOption": True},
                   {"stage": "feedback", "unknownFeedback": True},
                   {"stage": "scenario_intro", "extraScenarioButton": True},
                   {"stage": "scenario_intro", "previewUnknownButton": True},
                   {"stage": "scenario_intro", "extraButton": True},
                   {"stage": "evasion", "extraInput": True})
        hidden_flip = {"stage": "inversion", "hiddenFlip": True}
        transient = {"stage": "feedback", "transient": True}
        preview_transition = {"stage": "scenario_intro", "previewTransition": True}
        for case in (*valid, *invalid, hidden_flip, transient, preview_transition):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.DEFENCE4_JS,
                    "frame": DEFENCE4_FRAME, "lesson": DEFENCE4_LESSON}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                if case in invalid:
                    self.assertIn("error", value["before"])
                    self.assertEqual(value["clicks"], [])
                elif case == hidden_flip:
                    self.assertEqual(value["before"]["stage"], "inversion")
                    self.assertIn("error", value["actions"][3])
                    self.assertEqual(value["clicks"], ["expand-0", "expand-1", "expand-2"])
                elif case in (transient, preview_transition):
                    self.assertIn("pending", value["before"])
                    self.assertEqual(value["clicks"], [])
                else:
                    self.assertEqual(value["before"]["stage"],
                                     "scenario" if case["stage"].startswith("choice") or
                                     case["stage"] == "feedback" else case["stage"])
                    if case["stage"] in {"evasion", "poisoning", "inversion",
                                         "scenario_intro"}:
                        self.assertIn("error", value["premature"])
                    if case["stage"] == "poisoning":
                        self.assertEqual(value["actions"][-1]["clicked"], "expand")
                        self.assertEqual(len(value["actions"]), 3)
                    if case["stage"] == "inversion":
                        self.assertEqual(value["actions"][-1]["clicked"], "flip")
                        self.assertEqual(len(value["actions"]), 6)
                    if case["stage"] == "terminal":
                        self.assertEqual(value["before"]["scenario_state"], "terminal")
                        self.assertIn("error", value["done"])
                        self.assertNotIn("start-over", value["clicks"])
                    else:
                        self.assertNotIn("error", value["done"])
                        if case["stage"].startswith("choice"):
                            self.assertEqual(value["done"]["clicked"], "scenario_choice")
                            chosen = (0, 0, 1, 0, 0)[case["choiceIndex"]]
                            self.assertEqual(value["clicks"], [f"choice-{chosen}"])
    def test_genai4_inner_script_requires_exact_activities_and_terminal(self):
        harness = r'''
const fs=require('node:fs'),vm=require('node:vm');
const data=JSON.parse(fs.readFileSync(0,'utf8'));
const base=data.frame.replace(/index\.html$/,'');
const stage=data.stage.startsWith('choice')||data.stage==='feedback'?
 'scenario':data.stage;
const names=['Text moderation','Image moderation','Multimodal moderation',
 'Output filtering','Input filtering'];
const panelStarts=[
 'Analyses, reviews, and filters harmful content in input prompts and outputs of GenAI models.',
 'This blocks images that contain offensive or unsafe content.',
 'Multimodal content moderation analyses and reviews content in multiple modes',
 'Analyses and filters if the output from the GenAI models',
 'Analyses and blocks if the input (prompt) to the GenAI models'];
const prompts=[
 'Hi again! Remember me? Chantelle Patel from the Business intelligence department.',
 'You were so helpful last time, could I run a few ideas past you again?',
 'unique challenge in implementing AI security controls for LLM-based AI systems compared to general AI systems?',
 'How do you think metaprompts help the AI system to prevent jailbreaking?',
 'How can we improve the accuracy, trust and relevance of the outputs provided by our recommendation system?',
 "Are you happy with the advice you've given or would you like to revisit the scenario?"];
const options=[
 ['Hi Chantelle, what can I help with today?'],['Happy to help'],
 ['Prompt injection','Model stealing','Training data poisoning'],
 ['Analyses, reviews, and filters harmful content in input prompts and outputs',
  'Analyses and filters outputs',
  'Provide instructions to the system on what it should output, or how to behave'],
 ['Use Retrieval-Augmented Generation (RAG) services',
  'Use content moderation techniques','Implement prompt engineering techniques'],
 ["Let's leave it there, we can pick this up again later.",
  'I want to do the scenario again.']];
const pick=[0,0,0,2,0,0];
const choiceIndex=Number(data.choiceIndex||0);
let top=0,openIndex=data.doubleOpen?1:-1,clicks=[];
const button=(text,extra={})=>({innerText:text,textContent:text,type:'button',
 getClientRects:()=>[1],getAttribute:()=>null,scrollIntoView:()=>{},
 parentElement:null,hidden:false,inert:false,click:()=>clicks.push(text),...extra});
const nav=['Skip to lesson','Navigation','Search','Close search'].map(text=>button(text));
const accordions=names.map((name,index)=>button(
 data.wrongAccordion&&index===2?'Other moderation':name,{
 getClientRects:()=>data.hiddenAccordion&&index===0?[]:[1],
 getAttribute:key=>key==='aria-controls'?'panel-'+index:
  key==='aria-expanded'?(openIndex===index||data.doubleOpen&&index===0?
    'true':'false'):null,
 click:()=>{openIndex=index;clicks.push('open-'+index)}
}));
if(data.extraAccordion)accordions.push(button('Unexpected accordion'));
if(data.missingAccordion)accordions.pop();
const cont=button(stage==='scenario_intro'?'Bring on the scenario!':'CONTINUE');
const responses=data.stage.startsWith('choice')?options[choiceIndex].map((text,index)=>
 button((index+1)+' '+(data.wrongOption&&index===0?'Unknown choice':text),{
 click:()=>clicks.push('pick-'+index)})):[];
const feedback=stage==='scenario'&&data.stage==='feedback'?button('CONTINUE',{
 matches:key=>key==='button.scenario-block__text__continue,button.scenario-block__dialogue__button',
 click:()=>clicks.push('feedback-continue')}):null;
const startOver=stage==='terminal'?button(data.terminalWrongControl?'RETRY':'START OVER',{
 matches:key=>key==='button.scenario-block__text__end',
 click:()=>clicks.push('start-over')}):null;
const earlyScenario=stage==='scenario_intro'&&
 (data.transientKnownIntro||data.transientUnknownButton||
  data.transientWrongText||data.transientExtraButton)?
 button(data.transientUnknownButton?'WRONG':'CONTINUE',{
 matches:key=>!data.transientUnknownButton&&
  key==='button.scenario-block__text__continue'}):null;
const scenarioButtons=stage==='terminal'?[startOver]:
 stage==='scenario'?(data.transient?[]:data.stage==='feedback'?[feedback]:responses):
 earlyScenario?(data.transientExtraButton?
  [earlyScenario,button('Extra action')]:[earlyScenario]):
 data.extraScenarioButton?[button('Unknown preview')]:[];
const scenario=stage==='reading'?null:{
 classList:{contains:key=>key==='scenario-block--initiated'&&
  stage!=='scenario_intro'},
 innerText:stage==='terminal'?
  "Scenario End You've helped me grasp the challenges of LLM security. I'll recommend secure measures for the LLM-based system.":
  data.unknownFeedback?
   'AI Security scenario 2 Earlier text. Unexpected new feedback. CONTINUE':
  data.stage==='feedback'?
   "AI Security scenario 2 Earlier text. I was hoping you'd say that. CONTINUE":
  earlyScenario?(data.transientWrongText?
   'AI Security scenario 2 Unexpected first question CONTINUE':
   'AI Security scenario 2 How to improve appropriate AI security controls on the system at XYZCo? CONTINUE'):
  'AI Security scenario 2 '+prompts[choiceIndex],
 querySelectorAll:key=>key==='button,[role="button"]'?scenarioButtons:
  key==='button.scenario-block__response'?responses:[],
 querySelector:key=>key==='.scenario-block__dialogue'?
  {innerText:prompts[choiceIndex]}:null};
const readingBase='Defences for attacks on GenAI models LESSON 1 OF 1 '+
 'Generative AI (GenAI) models are trained on large datasets. ';
const later=stage==='reading'?' ':
 'Security-enhancing technologies in AI application development. '+
 (data.terminalMissingSummary?' ':
 'This activity helped highlight the unique challenges of LLM-based systems. ');
const prefix=readingBase+later;
const padding=data.terminalLength?
 data.terminalLength-prefix.length:
 {reading:3800,scenario_intro:5100,scenario:5500,terminal:6200}[stage];
const max={reading:3700,scenario_intro:4955,scenario:5500,terminal:6233}[stage];
const wrap={innerText:prefix+'X'.repeat(padding),clientHeight:550,
 scrollHeight:(data.shortScroll?100:max)+550,
 get scrollTop(){return top},set scrollTop(value){top=value}};
const search={type:'search',disabled:true,
 matches:key=>key==='input[type="search"][disabled]'};
const form={matches:key=>key==='form.nav-sidebar-search--disabled',
 querySelectorAll:key=>key==='input'?[search]:[]};
const continueButtons=stage==='scenario'||stage==='terminal'||
 data.transientIntro||earlyScenario?[]:[cont];
const allButtons=[...nav,...accordions,...continueButtons,...scenarioButtons,
 ...(data.extraButton?[button('Submit answer')]:[])];
const page={URL:base+'scorm-content/scormcontent/index.html#/lessons/'+
 (data.wrongLesson?'other-lesson':data.lesson),
 title:data.wrongPageTitle?'Other title':data.simplePageTitle?
  'Defences for attacks on GenAI models':
  'Defences for attacks on GenAI models - Defences for attacks on GenAI models',
 readyState:'complete',body:{innerText:'LESSON 1 OF 1'},
 defaultView:{getComputedStyle:()=>({display:'block',visibility:'visible'})},
 getElementById:key=>key.startsWith('panel-')?{
  textContent:data.wrongPanel&&key==='panel-2'?'Other content':
   panelStarts[Number(key.slice(6))]+' Additional explanation.',
  innerText:panelStarts[Number(key.slice(6))]+' Additional explanation.'}:null,
 querySelectorAll:key=>({
  iframe:[],'div.page-wrap':[wrap],
  'button.blocks-accordion__header':accordions,
  'button.continue-btn':continueButtons,
  'button.visually-hidden.btn-skip-to-lesson,button.nav-control__button,button.nav-sidebar-header__search-btn,button.nav-sidebar-search__close':nav,
  'button[data-rmiz-btn-zoom]':[],
  'button[data-rmiz-btn-unzoom]':[],
  'button,[role="button"]':allButtons,
  input:data.extraInput?[search,{}]:[search],form:[form]
 }[key]||[]),
 querySelector:key=>key==='.nav-sidebar-header__progress-text'?
  {innerText:'0% COMPLETE'}:key==='.scenario-block'?scenario:null};
const content={title:data.wrongFrameTitle?'Wrong lesson':
 'Defences for attacks on GenAI models',contentDocument:page};
const auxiliary=[
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/AICCComm.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'},
 {title:'Intentionally blank',src:base+'scorm-content/scormdriver/blank.html'}];
const driver={readyState:'complete',querySelectorAll:key=>
 key==='iframe#content-frame'?[content]:key==='iframe'?[content,...auxiliary]:[],
 querySelector:()=>null};
const container={src:base+'scorm-content/scormdriver/indexAPI.html',
 contentDocument:driver};
const document={readyState:'complete',querySelectorAll:key=>
 key==='iframe[title="container"]'||key==='iframe'?[container]:[],
 querySelector:()=>null};
const context={document,location:{href:data.wrongFrame?
 data.frame.replace('fb3d4742','wrong-asset'):data.frame}};
const run=(action,extra={})=>vm.runInNewContext(data.script+'('+JSON.stringify({
 frame:data.frame,heading:'Defences for attacks on GenAI models',
 lesson:data.lesson,action,...extra})+')',context);
const before=run('inspect');
let premature=null,actions=[],done=null;
if(!before.error&&!before.pending){
 if(stage==='reading'){
   premature=run('continue',{accordions_reviewed:true});
   for(let index=0;index<5;index++)actions.push(run('open_accordion',{index}));
   top=max;done=run('continue',{accordions_reviewed:true});
 }else if(stage==='scenario_intro'){
   premature=run('continue');top=max;done=run('continue');
 }else if(stage==='scenario'){
   done=run(data.stage==='feedback'?'scenario_continue':'scenario_choice');
 }else done=run('scenario_choice');
}
process.stdout.write(JSON.stringify({before,premature,actions,done,clicks,top}));
'''
        valid = ({"stage": "reading"},
                 {"stage": "reading", "simplePageTitle": True},
                 {"stage": "scenario_intro"},
                 *({"stage": f"choice{index}", "choiceIndex": index}
                   for index in range(6)),
                 {"stage": "feedback"}, {"stage": "terminal"},
                 {"stage": "terminal", "terminalLength": 6067})
        invalid = ({"stage": "reading", "wrongFrame": True},
                   {"stage": "reading", "wrongLesson": True},
                   {"stage": "reading", "wrongFrameTitle": True},
                   {"stage": "reading", "wrongPageTitle": True},
                   {"stage": "reading", "wrongAccordion": True},
                   {"stage": "reading", "wrongPanel": True},
                   {"stage": "reading", "extraAccordion": True},
                   {"stage": "reading", "missingAccordion": True},
                   {"stage": "reading", "hiddenAccordion": True},
                   {"stage": "reading", "doubleOpen": True},
                   {"stage": "reading", "shortScroll": True},
                   {"stage": "reading", "extraButton": True},
                   {"stage": "reading", "extraInput": True},
                   {"stage": "choice2", "choiceIndex": 2, "wrongOption": True},
                   {"stage": "feedback", "unknownFeedback": True},
                   {"stage": "scenario_intro", "extraScenarioButton": True},
                   {"stage": "scenario_intro", "transientUnknownButton": True},
                   {"stage": "scenario_intro", "transientWrongText": True},
                   {"stage": "scenario_intro", "transientExtraButton": True},
                   {"stage": "terminal", "terminalLength": 5999},
                   {"stage": "terminal", "terminalLength": 6067,
                    "terminalWrongControl": True},
                   {"stage": "terminal", "terminalLength": 6067,
                    "terminalMissingSummary": True})
        transients = ({"stage": "feedback", "transient": True},
                      {"stage": "scenario_intro", "transientIntro": True},
                      {"stage": "scenario_intro", "transientKnownIntro": True})
        for case in (*valid, *invalid, *transients):
            with self.subTest(case=case):
                result = subprocess.run(["node", "-e", harness], input=json.dumps({
                    **case, "script": flow.GENAI4_JS,
                    "frame": GENAI4_FRAME, "lesson": GENAI4_LESSON}),
                    text=True, capture_output=True, check=True)
                value = json.loads(result.stdout)
                if case in invalid:
                    self.assertIn("error", value["before"], value)
                    self.assertEqual(value["clicks"], [])
                elif case in transients:
                    self.assertIn("pending", value["before"], value)
                    self.assertEqual(value["clicks"], [])
                else:
                    self.assertEqual(value["before"]["stage"],
                                     "scenario" if case["stage"].startswith("choice") or
                                     case["stage"] == "feedback" else case["stage"])
                    if case["stage"] == "reading":
                        self.assertIn("error", value["premature"])
                        self.assertEqual(
                            [entry["clicked"] for entry in value["actions"]],
                            ["open_accordion"] * 5)
                        self.assertEqual(value["done"]["clicked"], "CONTINUE")
                        self.assertEqual(value["clicks"][-1], "CONTINUE")
                    elif case["stage"] == "scenario_intro":
                        self.assertIn("error", value["premature"])
                        self.assertEqual(value["done"]["clicked"], "scenario_start")
                    elif case["stage"] == "terminal":
                        self.assertEqual(value["before"]["scenario_state"], "terminal")
                        self.assertIn("error", value["done"])
                        self.assertNotIn("start-over", value["clicks"])
                    else:
                        self.assertNotIn("error", value["done"], value)
                        if case["stage"].startswith("choice"):
                            self.assertEqual(value["clicks"],
                                             [f"pick-{(0, 0, 0, 2, 0, 0)[case['choiceIndex']]}"])


if __name__ == "__main__":
    unittest.main()

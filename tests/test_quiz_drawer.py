"""Regression for Coursera's modal course outline on quiz landing pages."""

import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('course_quiz_drawer', ROOT / 'coursera_start.py')
course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(course)


def javascript(*cases):
    result = subprocess.run(['node', str(ROOT / 'tests/second_submit_dom.js')],
        input=json.dumps({'code': course.QUIZ_UI_JS,
                          'notice': course.AUTHORIZED_QUIZ_NOTICE, 'cases': cases}),
        capture_output=True, text=True, encoding='utf-8', check=True)
    return json.loads(result.stdout)


DISMISS = {'action': 'dismiss_course_sidebar'}


class CourseSidebarDrawerDOMTests(unittest.TestCase):
    def test_observed_cover_exposes_start_after_drawer_closes(self):
        value = javascript({'noPopup': True, 'courseCover': True, 'courseDrawer': True,
                            'followup': DISMISS, 'inspectAfter': True})[0]
        self.assertEqual(value['before']['dialog_kind'], 'course_sidebar_drawer')
        self.assertEqual(value['before']['available_buttons'], [])
        self.assertEqual(value['result']['clicked'], 'Close')
        self.assertEqual(value['after']['available_buttons'], ['Start assignment'])
        self.assertFalse(value['after']['blocking_dialog'])

    def test_exact_sidebar_is_closed_then_quiz_form_is_visible(self):
        value = javascript({'noPopup': True, 'courseDrawer': True,
                            'followup': DISMISS, 'inspectAfter': True})[0]
        self.assertEqual(value['before']['dialog_kind'], 'course_sidebar_drawer')
        self.assertTrue(value['before']['blocking_dialog'])
        self.assertEqual(value['before']['course_sidebar_close_count'], 1)
        self.assertEqual(value['result']['clicked'], 'Close')
        self.assertEqual(value['clicks'], ['drawer-close'])
        self.assertEqual(value['after']['dialog_kind'], 'quiz_form')
        self.assertTrue(value['after']['editable_form'])

    def test_changed_or_ambiguous_sidebar_does_not_click(self):
        for variation in ({'wrongDrawerTestid': True}, {'duplicateDrawer': True},
                          {'extraDrawerClose': True}, {'otherModal': True},
                          {'mutateDrawerText': 'Different course'}, {'removeDrawer': True}):
            with self.subTest(variation=variation):
                value = javascript({'noPopup': True, 'courseDrawer': True,
                                    'followup': DISMISS, **variation})[0]
                self.assertIn('error', value['result'])
                self.assertEqual(value['clicks'], [])

    def test_unknown_modal_remains_blocking(self):
        value = javascript({'noPopup': True, 'courseDrawer': True,
                            'otherModal': True})[0]['result']
        self.assertEqual(value['dialog_kind'], 'modal')
        self.assertTrue(value['blocking_dialog'])


class CourseSidebarDrawerRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_quiz_closes_sidebar_once_then_reinspects(self):
        state = javascript({'noPopup': True, 'courseDrawer': True,
                            'followup': DISMISS, 'inspectAfter': True})[0]
        actions = []

        async def quiz_ui(action='inspect', **params):
            actions.append((action, params))
            if action == 'inspect':
                return state['before'] if len(actions) == 1 else state['after']
            self.assertEqual(action, 'dismiss_course_sidebar')
            self.assertEqual(params['snapshot_id'], state['before']['snapshot_id'])
            self.assertEqual(params['expected_dialog'], state['before']['dialog'])
            return state['result']

        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(max_steps=40), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                result = await runner.prepare_quiz({'key': 'assignment:UAOb6'}, quiz_ui)
        self.assertTrue(result['editable_form'])
        self.assertEqual([action for action, _ in actions],
                         ['inspect', 'dismiss_course_sidebar', 'inspect'])

    async def test_prepare_quiz_waits_for_delayed_drawer_removal_without_reclicking(self):
        state = javascript({'noPopup': True, 'courseDrawer': True,
                            'followup': DISMISS, 'inspectAfter': True})[0]
        actions = []

        async def quiz_ui(action='inspect', **params):
            actions.append(action)
            if action == 'dismiss_course_sidebar':
                return state['result']
            return state['before'] if actions.count('inspect') <= 3 else state['after']

        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(max_steps=40), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                result = await runner.prepare_quiz({'key': 'assignment:UAOb6'}, quiz_ui)
        self.assertTrue(result['editable_form'])
        self.assertEqual(actions.count('dismiss_course_sidebar'), 1)
        self.assertEqual(actions.count('inspect'), 4)

    async def test_prepare_quiz_stops_if_drawer_never_closes(self):
        state = javascript({'noPopup': True, 'courseDrawer': True,
                            'followup': DISMISS})[0]
        actions = []

        async def quiz_ui(action='inspect', **params):
            actions.append(action)
            return state['result'] if action == 'dismiss_course_sidebar' else state['before']

        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(max_steps=40), Path(directory))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()):
                with self.assertRaisesRegex(course.StopRun, 'sidebar remained open'):
                    await runner.prepare_quiz({'key': 'assignment:UAOb6'}, quiz_ui)
        self.assertEqual(actions.count('dismiss_course_sidebar'), 1)
        self.assertEqual(actions.count('inspect'), 12)


if __name__ == '__main__':
    unittest.main(verbosity=2)

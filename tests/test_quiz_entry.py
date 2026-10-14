"""Offline regression for an entry button disappearing as the quiz opens."""
import copy
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    'course_quiz_entry', ROOT / 'coursera_start.py')
course = importlib.util.module_from_spec(spec)
spec.loader.exec_module(course)
LOG = ROOT / 'tests/fixtures/logs/course_v3_20261014_152205_701156/progress.json'
CAPTURED = [event for event in json.loads(LOG.read_text(encoding='utf-8'))['events']
            if event['event'] == 'quiz_controls'][-8:]
ITEM = {'key': CAPTURED[-1]['key']}
ENTRY = copy.deepcopy(CAPTURED[1]['result'])
FORM = copy.deepcopy(CAPTURED[-1]['result'])
for field in ('error', 'error_code', 'matches'):
    FORM.pop(field, None)


class QuizEntryTests(unittest.IsolatedAsyncioTestCase):
    async def run_prepare(self, quiz_ui):
        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(max_steps=40), Path(directory))
            runner.solve_quiz_questions = AsyncMock(side_effect=AssertionError('No model request during entry'))
            with patch.object(course.asyncio, 'sleep', new=AsyncMock()), redirect_stdout(io.StringIO()):
                result = await runner.prepare_quiz(ITEM, quiz_ui)
            runner.solve_quiz_questions.assert_not_awaited()
            return result

    async def test_exact_logged_start_to_resume_race_accepts_fresh_form(self):
        events = iter(CAPTURED + [{'action': 'inspect', 'result': FORM}])
        calls = []

        async def quiz_ui(action='inspect', **params):
            event = next(events)
            self.assertEqual(action, event['action'])
            calls.append((action, params))
            if action == 'button':
                self.assertEqual(params['label'],
                    'Resume assignment' if event['result'].get('error') else 'Start assignment')
            return copy.deepcopy(event['result'])

        result = await self.run_prepare(quiz_ui)
        self.assertTrue(result['editable_form'])
        self.assertEqual(len(result['controls']), 24)
        self.assertEqual([p['label'] for a, p in calls if a == 'button'],
                         ['Start assignment', 'Resume assignment'])
        self.assertEqual(calls[-1][0], 'inspect', 'Refresh after a pre-click miss')
        with self.assertRaises(StopIteration):
            next(events)

    async def test_zero_match_transient_can_retry_until_entry_clicks(self):
        views = iter([ENTRY, ENTRY, FORM])
        outcomes = iter([{'error': 'Expected one enabled matching button.',
                          'error_code': 'button_not_ready', 'matches': 0},
                         {'clicked': 'Start assignment'}])
        calls = []

        async def quiz_ui(action='inspect', **params):
            calls.append(action)
            return copy.deepcopy(next(views) if action == 'inspect' else next(outcomes))

        result = await self.run_prepare(quiz_ui)
        self.assertTrue(result['editable_form'])
        self.assertEqual(calls, ['inspect', 'button', 'inspect', 'button', 'inspect'])

    async def assert_entry_failure_is_terminal(self, failure):
        calls = []

        async def quiz_ui(action='inspect', **params):
            calls.append(action)
            return copy.deepcopy(ENTRY if action == 'inspect' else failure)

        with self.assertRaisesRegex(course.StopRun, 'Quiz entry button was not clicked'):
            await self.run_prepare(quiz_ui)
        self.assertEqual(calls, ['inspect', 'button'])

    async def test_ambiguous_entry_failure_stops_without_retry(self):
        await self.assert_entry_failure_is_terminal({
            'error': 'Expected one enabled matching button.',
            'error_code': 'ambiguous_button', 'matches': 2})

    async def test_unknown_or_partial_failure_stops_without_retry(self):
        for failure in [{'error': 'Unknown UI failure'},
                        {'error': 'Button disappeared after click.', 'error_code': 'partial_action'}]:
            with self.subTest(failure=failure):
                await self.assert_entry_failure_is_terminal(failure)

    async def test_successful_entry_is_not_clicked_again_while_loading(self):
        views = iter([ENTRY, ENTRY, ENTRY, FORM])
        clicks = []

        async def quiz_ui(action='inspect', **params):
            if action == 'inspect':
                return copy.deepcopy(next(views))
            clicks.append(params['label'])
            return {'clicked': params['label']}

        self.assertTrue((await self.run_prepare(quiz_ui))['editable_form'])
        self.assertEqual(clicks, ['Start assignment'])

    async def test_pre_click_misses_are_bounded_by_existing_deadline(self):
        clock = {'now': 0.0}
        calls = []

        async def fake_sleep(delay):
            self.assertGreater(delay, 0, 'A transient retry must yield between inspections')
            clock['now'] += delay

        async def quiz_ui(action='inspect', **params):
            calls.append(action)
            if action == 'inspect':
                return copy.deepcopy(ENTRY)
            return {'error': 'Expected one enabled matching button.',
                    'error_code': 'button_not_ready', 'matches': 0}

        with tempfile.TemporaryDirectory() as directory:
            runner = course.CourseRunner(None, SimpleNamespace(max_steps=40), Path(directory))
            with patch.object(course, 'time', SimpleNamespace(monotonic=lambda: clock['now'])), \
                    patch.object(course.asyncio, 'sleep', side_effect=fake_sleep), \
                    redirect_stdout(io.StringIO()):
                with self.assertRaisesRegex(course.StopRun, 'did not load within 35 seconds'):
                    await runner.prepare_quiz(ITEM, quiz_ui)
        self.assertEqual(clock['now'], 35)
        self.assertGreater(calls.count('button'), 1)
        self.assertEqual(calls.count('inspect'), calls.count('button'))

    async def test_new_blocking_dialog_after_miss_stops(self):
        views = iter([ENTRY, {'blocking_dialog': True, 'dialog_kind': 'modal',
                             'dialog': 'Confirm your identity'}])

        async def quiz_ui(action='inspect', **params):
            if action == 'inspect':
                return copy.deepcopy(next(views))
            return {'error': 'Expected one enabled matching button.',
                    'error_code': 'button_not_ready', 'matches': 0}

        with self.assertRaisesRegex(course.StopRun, 'separate quiz dialog needs manual review'):
            await self.run_prepare(quiz_ui)


if __name__ == '__main__':
    unittest.main(verbosity=2)

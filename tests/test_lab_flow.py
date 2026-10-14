"""Offline regression checks for launching and verifying Coursera labs."""

import importlib
import json
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import AsyncMock, patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
lab = importlib.import_module('lab_flow')
engine = importlib.import_module('coursera_start')
COURSE = 'https://www.coursera.org/learn/deep-learning-with-pytorch'
LESSON = COURSE + '/ungradedLab/VZXic/lab-build-and-visualize-a-perceptron-from-scratch'
ITEM = {'key': 'ungradedLab:VZXic', 'type': 'ungradedLab', 'title': 'Lab - Build and Visualize a Perceptron from Scratch',
        'url': LESSON}


class FakeTab:
    def __init__(self, mode='popup', *, popup=True, already_marked=False,
                 hide_actions_when_marked=False, foreign_popup=False, mark_ready_after=0,
                 goal_modal=False, sidebar_drawer=False, outline_toggle=False):
        self.address = 'ws://127.0.0.1:9223/devtools/page/source'
        self.url = LESSON
        self.mode = mode
        self.popup_enabled = popup
        self.foreign_popup = foreign_popup
        self.marked = already_marked
        self.hide_actions_when_marked = hide_actions_when_marked
        self.launched = False
        self.actions = []
        self.popup_url = 'https://labs.coursera.org/lab/session'
        self.popup_closed = False
        self.mark_ready_after = mark_ready_after
        self.post_launch_inspects = 0
        self.goal_modal = goal_modal
        self.sidebar_drawer = sidebar_drawer
        self.outline_toggle = outline_toggle

    async def evaluate(self, expression):
        if expression.startswith(lab.LAB_PAGE_JS):
            action = ('dismiss_goal' if '"action": "dismiss_goal"' in expression else
                      'close_drawer' if '"action": "close_drawer"' in expression else
                      'launch' if '"action": "launch"' in expression else
                      'mark' if '"action": "mark"' in expression else 'inspect')
            if self.url != LESSON:
                return {'error': 'The selected tab is not the expected Coursera lab.', 'url': self.url}
            if self.goal_modal:
                if action == 'dismiss_goal':
                    self.actions.append('dismiss_goal')
                    self.goal_modal = False
                    return {'clicked': 'dismiss_goal', 'url': LESSON}
                return {'goal_modal': True, 'url': LESSON}
            if self.sidebar_drawer:
                if action == 'close_drawer':
                    self.actions.append('close_drawer')
                    self.sidebar_drawer = False
                    return {'clicked': 'close_drawer', 'url': LESSON}
                return {'sidebar_drawer': True, 'url': LESSON}
            if action == 'launch':
                self.actions.append('launch')
                self.launched = True
                if self.mode in {'same_tab', 'popup_and_redirect'}:
                    self.url = self.popup_url
                return {'clicked': 'launch', 'url': LESSON}
            if action == 'mark':
                self.actions.append('mark')
                self.marked = True
                return {'clicked': 'mark', 'url': LESSON}
            action_count = 0 if self.marked and self.hide_actions_when_marked else 1
            if self.launched:
                self.post_launch_inspects += 1
            return {'url': LESSON, 'ready': 'complete', 'heading': ITEM['title'],
                    'launch_count': action_count,
                    'mark_count': action_count if self.post_launch_inspects > self.mark_ready_after else 0,
                    'launch_disabled': False,
                    'mark_disabled': False, 'launch_href': self.popup_url}
        if expression.startswith(engine.LESSON_OUTLINE_JS):
            if self.outline_toggle:
                self.actions.append('open_outline')
                self.sidebar_drawer = True
                return {'clicked': True}
            return {'clicked': False, 'matches': 0}
        if expression == lab.EXTERNAL_READY_JS:
            return {'url': self.url, 'ready': 'complete', 'rendered': True, 'browser_error': False}
        raise AssertionError('Unexpected page script')

    async def call(self, method, params=None):
        if method == 'Target.getTargets':
            rows = [{'targetId': 'source', 'type': 'page', 'url': self.url}]
            if self.launched and self.mode in {'popup', 'popup_and_redirect'} and self.popup_enabled and not self.popup_closed:
                rows.append({'targetId': 'popup', 'type': 'page',
                             'url': 'https://unrelated.example.org/' if self.foreign_popup else self.popup_url,
                             **({} if self.foreign_popup else {'openerId': 'source'})})
            return {'targetInfos': rows}
        if method == 'Target.activateTarget':
            self.actions.append(('activate', params['targetId']))
            return {}
        if method == 'Target.closeTarget':
            self.actions.append(('close', params['targetId']))
            self.popup_closed = True
            return {'success': True}
        raise AssertionError('Unexpected CDP call')

    async def navigate(self, url):
        self.actions.append(('navigate', url))
        self.url = url


class LabWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.sleep = patch.object(lab.asyncio, 'sleep', new=AsyncMock())
        self.sleep.start()
        self.addCleanup(self.sleep.stop)
        self.probes = patch.multiple(lab, PAGE_PROBES=3, LAUNCH_PROBES=3, READY_PROBES=3)
        self.probes.start()
        self.addCleanup(self.probes.stop)

    async def test_new_popup_must_load_before_mark_and_green_verification(self):
        tab = FakeTab()
        events = []
        snapshots = iter([
            None,
            {'url': tab.popup_url, 'ready': 'complete', 'rendered': True, 'browser_error': False},
            {'url': tab.popup_url, 'ready': 'complete', 'rendered': True, 'browser_error': False},
        ])
        async def inspect(target_id):
            self.assertEqual(target_id, 'popup')
            return next(snapshots)
        async def verify(item, wait=0):
            self.assertEqual(item, ITEM)
            return tab.marked
        with patch.object(lab, '_external_target_snapshot', new=inspect):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify,
                                   event=lambda kind, **fields: events.append(kind))
        self.assertEqual(tab.actions, ['launch', ('close', 'popup'), ('activate', 'source'), 'mark'])
        self.assertEqual(events, ['lab_launched', 'lab_site_loaded', 'lab_popup_closed', 'lab_mark_clicked'])
        self.assertTrue(tab.marked)

    async def test_mark_control_may_appear_after_source_page_returns(self):
        tab = FakeTab(mark_ready_after=3)
        loaded = {'url': tab.popup_url, 'ready': 'complete', 'rendered': True, 'browser_error': False}
        async def verify(item, wait=0):
            return tab.marked
        with patch.object(lab, '_external_target_snapshot', new=AsyncMock(return_value=loaded)):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertGreaterEqual(tab.post_launch_inspects, 4)
        self.assertEqual(tab.actions[-1], 'mark')

    async def test_exact_daily_goal_dialog_is_dismissed_before_launch(self):
        tab = FakeTab(goal_modal=True)
        loaded = {'url': tab.popup_url, 'ready': 'complete', 'rendered': True, 'browser_error': False}
        async def verify(item, wait=0):
            return tab.marked
        with patch.object(lab, '_external_target_snapshot', new=AsyncMock(return_value=loaded)):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions[:2], ['dismiss_goal', 'launch'])
        self.assertTrue(tab.marked)

    async def test_sidebar_drawer_is_closed_for_actions_and_reopened_for_green_check(self):
        tab = FakeTab(sidebar_drawer=True, outline_toggle=True)
        loaded = {'url': tab.popup_url, 'ready': 'complete', 'rendered': True, 'browser_error': False}
        async def verify(item, wait=0):
            return tab.marked and tab.sidebar_drawer
        with patch.object(lab, '_external_target_snapshot', new=AsyncMock(return_value=loaded)):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions, ['close_drawer', 'launch', ('close', 'popup'),
                                       ('activate', 'source'), 'open_outline',
                                       'close_drawer', 'mark', 'open_outline'])
        self.assertTrue(tab.marked)

    async def test_transient_daily_goal_heading_does_not_hide_lesson(self):
        javascript = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
const body = {hidden:false, inert:false, parentElement:null,
  getAttribute:()=>null, querySelectorAll: selector => ({
    h1: [heading(payload.heading), heading("You've completed today's goals!")],
    'button,a[href],[role="button"]': [button('Launch lab')]
  }[selector] || [])};
function heading(value) { return {innerText:value, parentElement:body, hidden:false, inert:false,
  getClientRects:()=>[1], getAttribute:()=>null}; }
function button(value) { return {innerText:value, parentElement:body, hidden:false, inert:false,
  getClientRects:()=>[1], getAttribute:()=>null, matches:()=>false, closest:()=>null}; }
const document = {body, readyState:'complete', querySelectorAll:()=>[]};
const context = {document, URL, location:{href:payload.url},
  getComputedStyle:()=>({display:'block',visibility:'visible'})};
const answer = vm.runInNewContext(payload.script + '(' + JSON.stringify(payload.request) + ')', context);
process.stdout.write(JSON.stringify(answer));
'''
        payload = {'script': lab.LAB_PAGE_JS, 'url': LESSON, 'heading': ITEM['title'],
                   'request': {'path': '/learn/deep-learning-with-pytorch/ungradedLab/VZXic/lab-build-and-visualize-a-perceptron-from-scratch',
                               'heading': ITEM['title'], 'action': 'inspect'}}
        result = subprocess.run(['node', '-e', javascript], input=json.dumps(payload), text=True,
                                capture_output=True, check=True)
        page = json.loads(result.stdout)
        self.assertNotIn('error', page)
        self.assertEqual(page['heading'], ITEM['title'])
        self.assertEqual(page['launch_count'], 1)

    async def test_only_exact_course_modal_controls_may_be_clicked(self):
        javascript = r'''
const fs = require('node:fs');
const vm = require('node:vm');
const payload = JSON.parse(fs.readFileSync(0, 'utf8'));
let clicks = 0;
const element = (text, parent, attrs={}) => ({innerText:text, parentElement:parent,
  hidden:false, inert:false, getClientRects:()=>[1], getAttribute:name=>attrs[name] || null});
const body = element('', null);
const rendered = element('', body, {'aria-hidden':'true'});
const heading = element(payload.heading, rendered);
const modal = element(payload.modal_text, body);
const button = element(payload.button_text, modal, {'aria-label':payload.button_aria});
button.click = () => clicks++;
modal.querySelectorAll = selector => selector === 'button,[role="button"]' ? [button] : [];
body.querySelectorAll = selector => selector === 'h1' ? [heading] : [];
const document = {body, readyState:'complete', querySelectorAll:selector =>
  selector === payload.modal_selector ? [modal] : []};
const context = {document, URL, location:{href:payload.url},
  getComputedStyle:()=>({display:'block',visibility:'visible'})};
const answer = vm.runInNewContext(payload.script + '(' + JSON.stringify(payload.request) + ')', context);
process.stdout.write(JSON.stringify({answer,clicks}));
'''
        path = '/learn/deep-learning-with-pytorch/ungradedLab/VZXic/lab-build-and-visualize-a-perceptron-from-scratch'
        goal_selector = '[role="dialog"][aria-modal="true"][data-testid="scroll-container"]'
        drawer_selector = '[role="dialog"][aria-modal="true"][data-testid="drawer-toggle-drawer"]'
        for selector, modal_text, button_text, button_aria, action, expected_clicks in [
            (goal_selector, "You've completed today's goals! Continue to use this momentum to get you closer to completing this course. Continue learning",
             'Continue learning', '', 'dismiss_goal', 1),
            (goal_selector, 'Unrelated modal Continue learning', 'Continue learning', '', 'dismiss_goal', 0),
            (drawer_selector, 'Course outline', '', 'Close', 'close_drawer', 1),
            (drawer_selector, 'Course outline', 'Close', 'Unrelated', 'close_drawer', 0),
        ]:
            with self.subTest(modal_text=modal_text, button_aria=button_aria):
                payload = {'script': lab.LAB_PAGE_JS, 'url': LESSON, 'heading': ITEM['title'],
                           'modal_text': modal_text, 'modal_selector': selector,
                           'button_text': button_text, 'button_aria': button_aria,
                           'request': {'path': path, 'heading': ITEM['title'], 'action': action}}
                result = subprocess.run(['node', '-e', javascript], input=json.dumps(payload), text=True,
                                        capture_output=True, check=True)
                output = json.loads(result.stdout)
                self.assertEqual(output['clicks'], expected_clicks)
                if expected_clicks:
                    self.assertEqual(output['answer']['clicked'], action)

    async def test_same_tab_launch_returns_to_exact_lesson_before_mark(self):
        tab = FakeTab(mode='same_tab')
        async def verify(item, wait=0):
            return tab.marked
        await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions, ['launch', ('navigate', LESSON), 'mark'])
        self.assertEqual(tab.url, LESSON)

    async def test_popup_with_source_redirect_returns_to_exact_lesson(self):
        tab = FakeTab(mode='popup_and_redirect')
        loaded = {'url': tab.popup_url, 'ready': 'complete', 'rendered': True, 'browser_error': False}
        async def verify(item, wait=0):
            return tab.marked
        with patch.object(lab, '_external_target_snapshot', new=AsyncMock(return_value=loaded)):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions, ['launch', ('close', 'popup'), ('activate', 'source'), ('navigate', LESSON), 'mark'])

    async def test_existing_green_mark_does_not_relaunch_lab(self):
        tab = FakeTab(already_marked=True, hide_actions_when_marked=True)
        async def verify(item, wait=0):
            return tab.marked
        await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions, [])

    async def test_mark_click_without_persisted_green_mark_stops(self):
        tab = FakeTab(hide_actions_when_marked=True)
        async def verify(item, wait=0):
            return False
        loaded = {'url': tab.popup_url, 'ready': 'complete', 'rendered': True, 'browser_error': False}
        with patch.object(lab, '_external_target_snapshot', new=AsyncMock(return_value=loaded)):
            with self.assertRaisesRegex(engine.StopRun, 'green Coursera completion check'):
                await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions.count('mark'), 1)

    async def test_no_window_or_navigation_never_marks_lab(self):
        tab = FakeTab(popup=False)
        async def verify(item, wait=0):
            return False
        with self.assertRaisesRegex(engine.StopRun, 'did not open'):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions, ['launch'])

    async def test_unrelated_new_window_is_not_mistaken_for_lab(self):
        tab = FakeTab(foreign_popup=True)
        async def verify(item, wait=0):
            return False
        with self.assertRaisesRegex(engine.StopRun, 'did not link it to Launch lab'):
            await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions, ['launch'])

    async def test_unloaded_lab_never_marks_complete(self):
        tab = FakeTab()
        async def verify(item, wait=0):
            return False
        with patch.object(lab, '_external_target_snapshot', new=AsyncMock(return_value=None)):
            with self.assertRaisesRegex(engine.StopRun, 'did not finish loading'):
                await lab.complete_lab(tab, ITEM, COURSE, verify_completion=verify)
        self.assertEqual(tab.actions, ['launch'])

    async def test_mismatched_course_identity_rejected_before_browser_action(self):
        tab = FakeTab()
        wrong = {**ITEM, 'key': 'ungradedLab:different'}
        with self.assertRaisesRegex(engine.StopRun, 'does not match'):
            await lab.complete_lab(tab, wrong, COURSE, verify_completion=AsyncMock(return_value=False))
        self.assertEqual(tab.actions, [])


if __name__ == '__main__':
    unittest.main()

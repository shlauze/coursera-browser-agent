"""Grounding of resumed Role Play replies against the current coach turn."""

import json
import subprocess
import unittest
from unittest.mock import AsyncMock, patch

import coursera_start as engine
import role_play_flow as role_play


ITEM = {
    'key': 'coach:RyZW9',
    'title': 'After the Red Team: Presenting Security Test Findings to Leadership',
}
TASKS = [
    'Present methodology: Explain testing methods and attack vectors.',
    'Report findings: State vulnerabilities with severity ratings.',
    'Explain findings: Translate technical details for executives.',
    'Recommend remediation: Propose prioritized security improvements.',
    'Address questions: Defend testing rigor and confidence.',
]
SCENARIO = (
    'You are a Security Engineer meeting the CISO about a customer service AI. '
    'The exercise includes adversarial inputs, guardrail bypasses, and potential '
    'model extraction vulnerabilities. No measured rates are supplied.'
)
COACH = (
    'How do you plan to balance these security enhancements with the performance '
    'requirements of our customer service team?'
)


def state():
    return {
        'tasks': TASKS,
        'unfinished_tasks': [TASKS[1]],
        'task_complete': 4,
        'task_total': 5,
        'scenario': SCENARIO,
        'goal': 'Brief the CISO and defend priorities.',
        'last_coach_message': COACH,
        'transcript_valid': True,
        'user_message_count': 3,
        'last_user_followed_by_coach': True,
        'latest_prompt': (
            'Earlier learner text falsely claimed tested frequencies and access to logs. '
            + COACH + ' Tasks 4/5 complete Ask for a hint'),
    }


class RecordingRunner:
    def __init__(self):
        self.events = []

    def event(self, name, **fields):
        self.events.append((name, fields))


class RolePlayGroundingTests(unittest.IsolatedAsyncioTestCase):
    def test_payload_uses_exact_latest_coach_turn_and_unfinished_task(self):
        payload = role_play._answer_payload(ITEM, state(), 3)
        data = json.loads(payload['messages'][1]['content'])
        self.assertEqual(data['coach_and_conversation_excerpt'], COACH)
        self.assertEqual(data['unfinished_tasks'], [TASKS[1]])
        self.assertNotIn('falsely claimed', payload['messages'][1]['content'])
        self.assertIn('earlier learner messages', payload['messages'][0]['content'])

    def test_payload_rejects_missing_or_stale_exact_coach_turn(self):
        for change in (
            {'last_coach_message': ''},
            {'transcript_valid': False},
            {'last_user_followed_by_coach': False},
        ):
            with self.subTest(change=change), self.assertRaises(engine.StopRun):
                role_play._answer_payload(ITEM, {**state(), **change}, 3)

    def test_known_fallback_corrects_uncertainty_and_addresses_tradeoff(self):
        reply = role_play._grounded_fallback(ITEM, state())
        self.assertIsInstance(reply, str)
        self.assertTrue(80 <= len(reply) <= 2400)
        self.assertNotRegex(reply, r'\d')
        self.assertRegex(reply.lower(), r'provisional severity')
        self.assertRegex(reply.lower(), r'latency')
        self.assertRegex(reply.lower(), r'helpfulness')
        self.assertRegex(reply.lower(), r'not validated measurements')
        self.assertNotRegex(reply.lower(), r'we (?:tested|verified|confirmed|measured)')

    def test_fallback_requires_specific_scenario_prompt_and_remaining_task(self):
        for change in (
            {'scenario': 'Unrelated scenario about an unknown AI system.'},
            {'last_coach_message': 'Please introduce yourself.'},
            {'unfinished_tasks': [TASKS[3]]},
        ):
            with self.subTest(change=change):
                self.assertIsNone(role_play._grounded_fallback(
                    ITEM, {**state(), **change}))

    async def test_rejected_drafts_log_only_reasons_then_use_grounded_reply(self):
        replies = [
            {'message': {'content': json.dumps({'reply': 'We measured 93 cases and reviewed the logs.'})}},
            {'message': {'content': json.dumps({'reply':
                'We reviewed the actual logs and confirmed the guardrail bypass in production. '
                'We should report this as a completed incident to the board.'})}},
            {'message': {'content': json.dumps({'reply': 'Too short.'})}},
        ]
        runner = RecordingRunner()
        with patch.object(engine, 'request_groq_answers', AsyncMock(side_effect=replies)) as groq:
            reply = await role_play._draft_reply(runner, ITEM, state(), 3)
        self.assertEqual(groq.await_count, 3)
        self.assertIn('provisional severity', reply.lower())
        rejection = [fields for event, fields in runner.events
                     if event == 'role_play_draft_rejected']
        self.assertEqual([entry['reason'] for entry in rejection], [
            'invalid_length', 'claimed_completed_analysis', 'invalid_length'])
        self.assertTrue(all('reply' not in entry for entry in rejection))

    def test_live_shape_extracts_unfinished_row_and_last_coach_message(self):
        script = 'const run=' + role_play.ROLE_PLAY_JS + ';\n' + r'''
const url='https://www.coursera.org/learn/example-course/coach/RyZW9/lesson';
const titles=[
 'Present methodology: Explain testing methods and attack vectors.',
 'Report findings: State vulnerabilities with severity ratings.',
 'Explain findings: Translate technical details for executives.',
 'Recommend remediation: Propose prioritized security improvements.',
 'Address questions: Defend testing rigor and confidence.'];
const e=text=>({innerText:text,hidden:false,inert:false,parentElement:null,
 disabled:false,getClientRects(){return [{}]},getAttribute(){return null}});
const root=e('Role Play');
const rows=titles.map((title,i)=>{
 const row=e(title);row.getAttribute=key=>key==='aria-label'?
  (i===1?'Task 2: ':'Completed task: ')+title:null;
 row.querySelectorAll=s=>s==='span,[data-testid]'?[e(title)]:[];
 return row;
});
const list=e('');rows.forEach(row=>row.parentElement=list);
list.querySelectorAll=s=>s==='[role="listitem"]'?rows:[];
const taskList=e('');list.parentElement=taskList;
taskList.querySelectorAll=s=>s==='[role="list"]'?[list]:[];
const tab=e('Tasks 4/5 complete');taskList.parentElement=tab;
tab.contains=node=>node===taskList;
root.querySelectorAll=s=>s==='[role="tabpanel"]#tabpanel-tasks[aria-labelledby="tab-tasks"]'?
 [tab]:s==='[data-testid="ai-coach-chat-messages"]'?[chatRoot]:[];
const user=e('An earlier unsupported learner claim.');
const coach=e('How do you plan to balance security with performance requirements of our customer service team?');
user.getAttribute=k=>k==='data-testid'?'chat-message-user':null;
coach.getAttribute=k=>k==='data-testid'?'chat-message-llm':null;
user.querySelector=coach.querySelector=()=>null;
const chatRoot=e('');chatRoot.querySelectorAll=s=>s.includes('chat-message-user')?[user,coach]:[];
const end=e('End Role Play');
global.location=new URL(url);
global.document={readyState:'complete',body:root,
 querySelector:s=>s==='main,[role="main"]'?root:null,
 querySelectorAll:s=>s==='button,[role="button"]'?[end]:
 s==='[data-testid="learner-role-play-item"]'?[root]:
 s==='[data-testid="task-list"]'?[taskList]:[]};
global.getComputedStyle=()=>({display:'block',visibility:'visible'});
const state=run({url,action:'inspect'});
process.stdout.write(JSON.stringify({tasks:state.tasks,unfinished:state.unfinished_tasks,
 lastCoach:state.last_coach_message,complete:state.task_complete,total:state.task_total}));
'''
        result = subprocess.run(['node', '-'], input=script, text=True,
                                capture_output=True, encoding='utf-8')
        self.assertEqual(result.returncode, 0, result.stderr)
        observed = json.loads(result.stdout)
        self.assertEqual(observed['tasks'], TASKS)
        self.assertEqual(observed['unfinished'], [TASKS[1]])
        self.assertTrue(observed['lastCoach'].startswith('How do you plan'))
        self.assertEqual((observed['complete'], observed['total']), (4, 5))


if __name__ == '__main__':
    unittest.main()

import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from v2.store import Store, PendingActionError
from v2.control import Control
from v2.dashboard import runtime_status, runtime_html, Dashboard
from v2.assessments import _handle_assessment
from v2.handlers import HandlerError
from v2.runner import Runner, course_id, item_id
from v2.discovery import CourseCard
from v2.outline import ItemRecord, OutlineSnapshot

class ProgressTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.path=Path(tmp.name)/'state.db';self.s=Store(self.path)
    def test_restart_preserves_each_question_and_history(self):
        a=self.s.begin_action('c','q','assessment')
        for stage in ['answer_received','answer_filled']:
            self.s.record_stage(a,stage,question='hash',attempt=1)
        restored=Store(self.path).get_meta(f'progress:{a}')
        self.assertEqual(restored['questions']['hash']['stage'],'answer_filled')
        self.assertEqual([r['stage'] for r in restored['history']],['answer_received','answer_filled'])
    def test_submission_milestones_block_reentry_before_browser_or_groq(self):
        for stage in ['submitting','submitted','passed']:
            self.s.record_stage(1,stage)
            page=Mock();groq=Mock()
            with self.assertRaisesRegex(HandlerError,'reconciled'):
                _handle_assessment(page,lambda:None,groq=groq,store=Store(self.path),action_id=1)
            page.evaluate.assert_not_called();groq.answer.assert_not_called()
    def test_restart_reconciles_remote_mark_and_never_reopens_submission(self):
        Control(self.s).mark_running()
        card=CourseCard(title='Course',action='Resume',progress_percent=90,page_number=1,action_index=0,group_title='')
        cid=course_id(card);self.s.upsert_course(cid,'Course',status='started')
        for completed in [False,True]:
            item=ItemRecord(title='Exam',kind='Graded Assignment',completed=completed,url='https://www.coursera.org/learn/c/assignment-submission/x/exam',module='M',index=0)
            if not completed:
                a=self.s.begin_action(cid,item_id(item),'assessment',{'url':item.url})
                self.s.record_stage(a,'submitted')
            runner=Runner(Store(self.path),Control(self.s))
            with patch('v2.runner.open_course',return_value=Mock(url=item.url)),patch('v2.runner.scan_outline',return_value=OutlineSnapshot('Course',(item,),item.url)),patch('v2.runner.open_item') as click:
                runner._reconcile_course(Mock(),[card],cid)
                if not completed:
                    # A pending submission isolates this item while other work
                    # may continue. It must remain unresolved until Coursera
                    # supplies the completion mark on the next reconciliation.
                    pending = self.s.pending_actions()
                    self.assertEqual([row['id'] for row in pending], [a])
                    self.assertIsNone(pending[0]['outcome'])
                    self.assertIn((cid, item.url), runner.blocked_items)
                    self.assertEqual(self.s.get_meta(f'submission:{a}'), 'pending')
                    self.assertEqual(self.s.get_meta(f'progress:{a}')['stage'], 'submitted')
                    self.assertTrue(any(event['code'] == 'PENDING_ITEM_ISOLATED'
                                        for event in self.s.recent_events()))
                click.assert_not_called()
        self.assertEqual(self.s.get_meta(f'progress:{a}')['stage'],'result_confirmed')
        self.assertEqual(self.s.pending_actions(),[])
    def test_wait_status_countdown_pause_and_escape(self):
        Control(self.s).mark_running()
        self.s.set_meta('groq_wait',{'status':429,'retry_at':160,'reason':'rate_limit'})
        self.s.set_meta('groq_rate_limit',{'metric':'TPD'})
        self.s.set_meta('current_item','<script>')
        v=runtime_status(self.s,100)
        self.assertTrue(v['waiting']);self.assertEqual(v['remaining_seconds'],60)
        html=runtime_html(v)
        self.assertIn('UTC+5',html);self.assertIn('TPD',html);self.assertNotIn('<script>',html)
        Control(self.s).request_pause()
        self.assertFalse(runtime_status(self.s,100)['waiting'])
        self.assertFalse(runtime_status(self.s,170)['waiting'])
    def test_failed_stage_does_not_claim_completion(self):
        a=self.s.begin_action('c','q','assessment');self.s.record_stage(a,'failed')
        self.assertEqual(len(self.s.pending_actions()),1)
        self.assertEqual(self.s.get_meta(f'submission:{a}'),'failed')
    def test_database_failure_before_submit_prevents_click(self):
        q={'id':'q','text':'Q','kind':'single','options':[{'id':'a','text':'A'}]}
        state={'questions':[q],'snapshot':'same','path':'/attempt'}
        groq=Mock();groq.answer.return_value={'cannot_answer':False,'selected':['a']}
        original=self.s.record_stage
        def save(a,stage,**kw):
            if stage=='submitting':raise OSError('disk unavailable')
            original(a,stage,**kw)
        with patch('v2.assessments._wait_entry',return_value=state),patch('v2.assessments.inspect',return_value=state),patch('v2.assessments._wait_submit',return_value=state),patch('v2.assessments.act') as act,patch.object(self.s,'record_stage',side_effect=save):
            with self.assertRaises(OSError):_handle_assessment(Mock(),lambda:None,groq=groq,store=self.s,action_id=1)
        self.assertEqual([c.kwargs['action'] for c in act.call_args_list],['fill'])

    def test_crash_after_submit_keeps_intent_and_blocks_second_click(self):
        q={'id':'q','text':'Q','kind':'single','options':[{'id':'a','text':'A'}]}
        state={'questions':[q],'snapshot':'same','path':'/attempt'}
        groq=Mock();groq.answer.return_value={'cannot_answer':False,'selected':['a']}
        def click(page,current,**request):
            if request['action']=='button':
                self.assertEqual(Store(self.path).get_meta('submission:1'),'submitting')
                raise TimeoutError('response lost after click')
        with patch('v2.assessments._wait_entry',return_value=state),patch('v2.assessments.inspect',return_value=state),patch('v2.assessments._wait_submit',return_value=state),patch('v2.assessments.act',side_effect=click):
            with self.assertRaises(TimeoutError):_handle_assessment(Mock(),lambda:None,groq=groq,store=self.s,action_id=1)
        with patch('v2.assessments.act') as repeat:
            with self.assertRaises(HandlerError):_handle_assessment(Mock(),lambda:None,groq=groq,store=Store(self.path),action_id=1)
            repeat.assert_not_called()

if __name__=='__main__':unittest.main()

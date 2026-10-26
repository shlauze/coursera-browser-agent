"""Offline safety tests for queue orchestration and handler dispatch."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import ANY, Mock, patch

from v2.control import Control
from v2.discovery import CourseCard, DiscoveryError
from v2.handlers import HandlerError, handle
from v2.outline import ItemRecord, OutlineSnapshot
from v2.policy import ItemAction, decide
from v2.runner import (
    Runner,
    RunnerAttention,
    SkipCurrent,
    _certificate_matches,
    course_id,
    item_id,
)
from v2.store import Store


class _CertificatePage:
    def __init__(self, url: str, body: str) -> None:
        self.url = url
        self._body = body

    def locator(self, selector: str) -> Mock:
        if selector == "body":
            return Mock(inner_text=Mock(return_value=self._body))
        if selector == "h1,h2":
            return Mock(all_inner_texts=Mock(return_value=self._body.splitlines()[:1]))
        raise AssertionError(f"Unexpected certificate selector: {selector}")


class RunnerSafetyTests(unittest.TestCase):
    def test_missing_input_block_survives_restart_but_new_source_unblocks(self):
        self.store.set_meta("item_attention:"+item_id(self.item), {"course_id":self.cid,"url":self.item.url,"reason":"Learner-authored essay required for the personal authorship confirmation"})
        self.assertTrue(self.runner._input_blocked(self.cid,self.item))
        self.assertIsNone(self.runner._next_coursework(self.cid,[self.item]))
        self.store.set_meta("peer_source:"+self.item.url,{"text":"new source"})
        self.assertFalse(self.runner._input_blocked(self.cid,self.item))
        self.assertFalse(self.item.completed)

    def test_unsupported_visual_item_waits_without_claiming_completion(self):
        self.store.set_meta("item_attention:"+item_id(self.item),{"course_id":self.cid,"url":self.item.url,"reason":"Question includes visual content; text-only model cannot answer it"})
        self.assertTrue(self.runner._input_blocked(self.cid,self.item))
        self.assertFalse(self.item.completed)
        self.store.set_meta("retry_item:"+item_id(self.item),True)
        self.assertFalse(self.runner._input_blocked(self.cid,self.item))

    def test_transient_failure_and_explicit_retry_remain_eligible(self):
        key="item_attention:"+item_id(self.item)
        self.store.set_meta(key,{"course_id":self.cid,"url":self.item.url,"reason":"Timeout"})
        self.assertFalse(self.runner._input_blocked(self.cid,self.item))
        self.store.set_meta(key,{"course_id":self.cid,"url":self.item.url,"reason":"File assignment needs an explicitly assigned local file"})
        self.assertTrue(self.runner._input_blocked(self.cid,self.item))
        self.store.set_meta("retry_item:"+item_id(self.item),True)
        self.assertFalse(self.runner._input_blocked(self.cid,self.item))

    def test_available_course_precedes_known_input_blocked_course(self):
        other=CourseCard("Available","Resume",20,1,1)
        self.store.upsert_course(course_id(other),other.title)
        self.store.set_meta("course_attention:"+self.cid,self.card.title+": Remaining required items await learner input: Essay")
        with patch("v2.runner.discover_in_progress",return_value=[self.card,other]),patch.object(self.runner,"_reconcile"),patch.object(self.runner,"_process_course") as process:
            self.runner.run(Mock(page=Mock()))
        self.assertEqual(process.call_args_list[0].args[1],other)
        self.assertEqual(process.call_count,2)

    def test_two_pending_actions_share_one_live_outline(self):
        from dataclasses import replace
        second=replace(self.item,title="Second",url=self.item.url+"second")
        for item in (self.item,second):
            aid=self.store.begin_action(self.cid,item_id(item),"assessment",{"url":item.url})
            self.store.set_meta(f"submission:{aid}","submitting")
        page=Mock(url=self.item.url)
        with patch("v2.runner.open_course",return_value=page) as opened,patch("v2.runner.scan_outline",return_value=OutlineSnapshot(self.card.title,(self.item,second),page.url)) as scan:
            result=self.runner._reconcile_course(Mock(),[self.card],self.cid)
        opened.assert_called_once();scan.assert_called_once()
        self.assertEqual(result[0],page)
        self.assertEqual(len(self.store.pending_actions()),2)
        self.assertIn((self.cid,second.url),self.runner.blocked_items)

    def test_uncertain_practice_does_not_block_required_or_erase_ledger(self):
        from dataclasses import replace
        self.store.set_meta("required_assessments_first", True)
        practice=replace(self.item, kind="Practice Assignment", title="Practice Quiz")
        a=self.store.begin_action(self.cid,item_id(practice),"assessment",{"url":practice.url})
        self.store.set_meta(f"submission:{a}","submitting")
        with patch("v2.runner.open_course",return_value=Mock(url=practice.url)), patch("v2.runner.scan_outline",return_value=OutlineSnapshot(self.card.title,(practice,),practice.url)):
            self.runner._reconcile_course(Mock(),[self.card],self.cid)
        self.assertEqual(self.store.get_action(a)["status"],"pending")
        self.assertEqual(self.store.get_meta(f"submission:{a}"),"submitting")
        self.assertFalse(self.runner.deferred)

    def test_uncertain_required_still_blocks_repeat(self):
        from dataclasses import replace
        required=replace(self.item,kind="Graded Assignment",title="Practice Quiz")
        self.store.set_meta("required_assessments_first",True)
        a=self.store.begin_action(self.cid,item_id(required),"assessment",{"url":required.url})
        self.store.set_meta(f"submission:{a}","submitting")
        with patch("v2.runner.open_course",return_value=Mock(url=required.url)), patch("v2.runner.scan_outline",return_value=OutlineSnapshot(self.card.title,(required,),required.url)):
            self.runner._reconcile_course(Mock(),[self.card],self.cid)
            self.assertIn((self.cid,required.url),self.runner.blocked_items)
        self.assertEqual(self.store.get_action(a)["status"],"pending")

    def test_ambiguous_item_continues_other_required_without_claiming_completion(self):
        from dataclasses import replace
        first=replace(self.item,kind="Graded Assignment",title="Ambiguous")
        second=replace(first,title="Other",url=first.url+"other")
        page=Mock(url=first.url)
        def handler(page,kind,checkpoint,**kw):
            self.store.set_meta(f"submission:{kw['action_id']}","not_submitted")
            if len(self.runner.blocked_items)==0:raise HandlerError("Ambiguous answer options")
            return Mock(message="passed")
        done=replace(second,completed=True)
        with patch("v2.runner.open_course",return_value=page),patch("v2.runner.scan_outline",side_effect=[OutlineSnapshot(self.card.title,(first,second),page.url),OutlineSnapshot(self.card.title,(first,second),page.url),OutlineSnapshot(self.card.title,(first,done),page.url)]),patch("v2.runner.open_item",return_value=page),patch("v2.runner.handle",side_effect=handler) as h,patch.object(self.runner,"_verify_item",return_value=True),patch.object(self.runner,"_finish_course") as finish:
            with self.assertRaisesRegex(RunnerAttention,"await learner input"):self.runner._process_course(page,self.card)
        self.assertEqual(h.call_count,2);self.assertEqual(self.runner.completed_items,1)
        finish.assert_not_called()
        self.assertIsNotNone(self.store.get_meta("item_attention:"+item_id(first)))

    def test_model_failure_in_lab_continues_quiz_and_preserves_uncertain_submission(self):
        from dataclasses import replace
        from v2.groq_client import GroqError
        first = replace(self.item, kind='Programming Assignment', title='Large lab')
        second = replace(first, kind='Graded Assignment', title='Other quiz', url=first.url+'other')
        page = Mock(url=first.url)
        actions = []
        def handler(page, kind, checkpoint, **kw):
            actions.append(kw['action_id'])
            if len(actions) == 1:
                self.store.set_meta(f"submission:{kw['action_id']}", 'submitting')
                raise GroqError('Local context budget too small')
            return Mock(message='passed')
        done = replace(second, completed=True)
        outlines = [OutlineSnapshot(self.card.title, items, page.url) for items in
                    ((first, second), (first, second), (first, done))]
        with patch('v2.runner.open_course', return_value=page), patch('v2.runner.scan_outline', side_effect=outlines), \
             patch('v2.runner.open_item', return_value=page), patch('v2.runner.handle', side_effect=handler) as handle, \
             patch.object(self.runner, '_verify_item', return_value=True), patch.object(self.runner, '_finish_course') as finish:
            with self.assertRaisesRegex(RunnerAttention, 'await learner input'):
                self.runner._process_course(page, self.card)
        self.assertEqual([call.args[1] for call in handle.call_args_list], [ItemAction.PROGRAMMING_LAB, ItemAction.ASSESSMENT])
        self.assertEqual(self.runner.completed_items, 1)
        self.assertEqual(self.store.get_meta(f'submission:{actions[0]}'), 'submitting')
        self.assertIn(actions[0], [a['id'] for a in self.store.pending_actions()])
        finish.assert_not_called()

    def test_disconnected_lab_continues_same_course_without_replaying_submission(self):
        from dataclasses import replace
        from playwright.sync_api import Error
        from v2.notebooks import NotebookExecutor
        first = replace(self.item, kind='Programming Assignment', title='Disconnected lab')
        second = replace(first, kind='Graded Assignment', title='Available quiz', url=first.url+'other')
        done = replace(second, completed=True)
        page = Mock(url=first.url)
        outlines = [OutlineSnapshot(self.card.title, items, page.url) for items in
                    ((first, second), (first, second), (first, done))]
        failed_actions = []
        def handler(page, kind, checkpoint, **kwargs):
            if not failed_actions:
                aid = kwargs['action_id']
                failed_actions.append(aid)
                self.store.set_meta(f'submission:{aid}', 'submitting')
                frame = Mock()
                frame.evaluate.side_effect = Error('TypeError: Failed to fetch')
                NotebookExecutor(frame, Mock(), self.store, aid, checkpoint).call('snapshot')
            return Mock(message='passed')
        with patch('v2.runner.open_course', return_value=page), patch('v2.runner.scan_outline', side_effect=outlines), \
             patch('v2.runner.open_item', return_value=page), patch('v2.runner.handle', side_effect=handler) as handled, \
             patch.object(self.runner, '_verify_item', return_value=True):
            with self.assertRaisesRegex(RunnerAttention, 'await learner input'):
                self.runner._process_course(page, self.card)
        self.assertEqual(handled.call_count, 2)
        self.assertEqual(self.runner.completed_items, 1)
        self.assertEqual(self.store.get_action(failed_actions[0])['status'], 'pending')
        self.assertEqual(self.store.get_meta(f'submission:{failed_actions[0]}'), 'submitting')
        self.assertIn((self.cid, first.url), self.runner.blocked_items)

    def test_unsupported_item_does_not_block_other_required_work(self):
        from dataclasses import replace
        from v2.policy import Decision
        first = replace(self.item, kind='Graded Assignment', title='Unknown environment')
        second = replace(first, title='Supported quiz', url=first.url+'other')
        done = replace(second, completed=True)
        page = Mock(url=first.url)
        outlines = [OutlineSnapshot(self.card.title, items, page.url) for items in
                    ((first, second), (first, second), (first, done))]
        with patch('v2.runner.open_course', return_value=page), patch('v2.runner.scan_outline', side_effect=outlines), \
             patch('v2.runner.decide', side_effect=[Decision(ItemAction.REVIEW_REQUIRED, 'Unsupported environment'), Decision(ItemAction.ASSESSMENT, '')]), \
             patch('v2.runner.open_item', return_value=page), patch('v2.runner.handle', return_value=Mock(message='passed')) as handle, \
             patch.object(self.runner, '_verify_item', return_value=True), patch.object(self.runner, '_finish_course') as finish:
            with self.assertRaisesRegex(RunnerAttention, 'await learner input'):
                self.runner._process_course(page, self.card)
        handle.assert_called_once()
        self.assertIn((self.cid, first.url), self.runner.blocked_items)
        self.assertEqual(self.runner.completed_items, 1)
        finish.assert_not_called()

    def test_optional_exclusion_is_exact_scoped_and_not_completion(self):
        record={"url":self.item.url,"title":self.item.title,"evidence":"Optional Essay Question / Practice Peer-graded Assignment","authorized_skip":True}
        self.store.set_meta("verified_optional:"+self.cid,[record])
        self.assertTrue(self.runner._optional_excluded(self.cid,self.item))
        self.assertFalse(self.runner._optional_excluded("another-course",self.item))
        self.assertFalse(self.item.completed)
        record["authorized_skip"]=False
        self.store.set_meta("verified_optional:"+self.cid,[record])
        self.assertFalse(self.runner._optional_excluded(self.cid,self.item))

    def test_deferred_required_item_never_finishes_course(self):
        graded=ItemRecord(title="Essay",kind="Peer-graded Assignment",completed=False,url="https://www.coursera.org/learn/example/peer/id/essay",module="M",index=0)
        self.store.set_meta("deferred_items:"+self.cid,[{"url":graded.url,"authorized":True}])
        self.assertFalse(self.runner._deferred_item("other",graded))
        page=Mock(url=graded.url)
        with patch("v2.runner.open_course",return_value=page), patch("v2.runner.scan_outline",return_value=OutlineSnapshot(self.card.title,(graded,),graded.url)), patch.object(self.runner,"_finish_course") as finish, patch("v2.runner.open_item") as opened:
            with self.assertRaisesRegex(RunnerAttention,"await learner input"):
                self.runner._process_course(Mock(),self.card)
        finish.assert_not_called();opened.assert_not_called()
        self.assertEqual(self.store.pending_actions(),[])

    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name) / "run.sqlite3")
        self.control = Control(self.store)
        self.control.mark_running()
        self.runner = Runner(self.store, self.control)
        self.card = CourseCard(
            title="Deep Learning with PyTorch",
            action="Resume",
            progress_percent=99,
            page_number=1,
            action_index=0,
            group_title="",
        )
        self.cid = course_id(self.card)
        self.store.upsert_course(self.cid, self.card.title, status="started")
        self.item = ItemRecord(
            title="A Reading", kind="Reading", completed=False,
            url="https://www.coursera.org/learn/deep-learning-with-pytorch/supplement/a",
            module="Module 1", index=0,
        )

    def test_focused_failure_pauses_without_opening_another_course(self):
        from v2.runner import StopRequested
        self.store.set_meta("focus_course_id", self.cid)
        calls=[]
        def checkpoint():
            if self.control.get_state() == "paused": raise StopRequested("test pause")
        self.runner.checkpoint=checkpoint
        with patch.object(self.runner,"_process_course",side_effect=HandlerError("closed tab")) as process:
            with self.assertRaises(StopRequested):
                self.runner._run_focused(Mock(),[self.card],self.cid)
        process.assert_called_once()
        self.assertEqual(self.control.get_state(),"paused")
        self.assertEqual(self.store.get_meta("focus_blocker"),"closed tab")
        self.assertFalse(self.runner.deferred)

    def test_run_does_not_close_new_unowned_course_tabs(self):
        page=Mock(); page.context.pages=[page]
        other=Mock(); other.url="https://www.coursera.org/learn/foreign/home"
        def fail(*args):
            page.context.pages.append(other)
            raise HandlerError("failure")
        with patch.object(self.runner,"_cards",return_value=[self.card]), patch.object(self.runner,"_reconcile"), patch.object(self.runner,"_process_course",side_effect=fail):
            self.runner.run(Mock(page=page))
        other.close.assert_not_called()

    def _outline(self, *, completed: bool) -> OutlineSnapshot:
        item = ItemRecord(
            title=self.item.title, kind=self.item.kind, completed=completed,
            url=self.item.url, module=self.item.module, index=self.item.index,
        )
        return OutlineSnapshot(self.card.title, (item,), self.item.url or "")

    def test_pending_action_is_confirmed_from_green_outline_without_reclick(self) -> None:
        action_id = self.store.begin_action(self.cid, item_id(self.item), "assessment")
        opened = Mock(url=self.item.url)
        with patch("v2.runner.open_course", return_value=opened) as open_course, \
             patch("v2.runner.scan_outline", return_value=self._outline(completed=True)) as scan_outline, \
             patch("v2.runner.handle") as handle_item:
            self.runner._reconcile(Mock(), [self.card])
        self.assertEqual(self.store.get_action(action_id)["outcome"], "confirmed")
        self.assertEqual(self.store.pending_actions(), [])
        open_course.assert_called_once()
        scan_outline.assert_called_once_with(opened)
        handle_item.assert_not_called()

    def test_reconcile_evidence_uses_matched_item_url_when_resume_opens_another_item(self):
        action_id = self.store.begin_action(self.cid, item_id(self.item), "assessment", {"url": self.item.url})
        opened = Mock(url="https://www.coursera.org/learn/course/assignment-submission/other/quiz")
        with patch("v2.runner.open_course", return_value=opened), \
             patch("v2.runner.scan_outline", return_value=self._outline(completed=True)), \
             patch("v2.runner.handle") as handle_item:
            self.runner._reconcile(Mock(), [self.card])
        action = self.store.get_action(action_id)
        self.assertEqual(action["outcome"], "confirmed")
        self.assertEqual(action["evidence"]["url"], self.item.url)
        self.assertEqual(action["evidence"]["observed_page_url"], opened.url)
        handle_item.assert_not_called()

    def test_unfinished_reading_intent_is_cancelled_by_coursework_mode(self) -> None:
        action_id = self.store.begin_action(self.cid, item_id(self.item), "reading")
        with patch("v2.runner.open_course", return_value=Mock(url=self.item.url)) as open_course, \
             patch("v2.runner.scan_outline", return_value=self._outline(completed=False)):
            self.runner._reconcile(Mock(), [self.card])
        self.assertEqual(self.store.get_action(action_id)["outcome"], "cancelled")
        open_course.assert_not_called()
        self.assertEqual(self.store.pending_actions(), [])

    def test_unfinished_lab_intent_requires_review_before_repeating(self) -> None:
        action_id = self.store.begin_action(self.cid, item_id(self.item), "ungraded_lab")
        with patch("v2.runner.open_course", return_value=Mock(url=self.item.url)), \
             patch("v2.runner.scan_outline", return_value=self._outline(completed=False)):
            self.runner._reconcile(Mock(), [self.card])
        self.assertEqual(self.store.get_action(action_id)["status"], "pending")
        self.assertIn((self.cid,self.item.url), self.runner.blocked_items)
        self.assertNotIn(self.cid,self.runner.deferred)

    def test_disappeared_course_pending_action_reconciles_only_matching_certificate(self) -> None:
        action_id = self.store.begin_action(self.cid, item_id(self.item), "assessment")
        certificate_url = "https://www.coursera.org/account/accomplishments/verify/ABC123"
        with patch("v2.runner._verified_certificate_url", return_value=certificate_url) as certificate_check, \
             patch("v2.runner.open_course") as open_course:
            self.runner._reconcile(Mock(), [])
        certificate_check.assert_called_once_with(ANY, self.card.title)
        open_course.assert_not_called()
        self.assertEqual(self.store.get_action(action_id)["outcome"], "confirmed")
        course = self.store.get_course(self.cid)
        self.assertEqual(course["status"], "complete")
        self.assertEqual(course["certificate_url"], certificate_url)

    def test_disappeared_course_without_certificate_stays_pending(self) -> None:
        action_id = self.store.begin_action(self.cid, item_id(self.item), "assessment")
        with patch("v2.runner._verified_certificate_url", return_value=None):
            self.runner._reconcile(Mock(), [])
        self.assertEqual(self.store.get_action(action_id)["status"], "pending")
        self.assertNotEqual(self.store.get_course(self.cid)["status"], "complete")

    def test_skipping_current_course_stops_at_checkpoint(self) -> None:
        self.control.mark_running()
        self.store.set_meta("current_course_id", self.cid)
        self.store.set_course_skipped(self.cid)
        with self.assertRaises(SkipCurrent):
            self.runner.checkpoint()

    def test_unsupported_peer_item_is_not_opened_or_submitted(self) -> None:
        graded = ItemRecord(
            title="Capstone", kind="Peer-graded Assignment", completed=False,
            url="https://www.coursera.org/learn/deep-learning-with-pytorch/programming/a",
            module="Module 4", index=0,
        )
        self.control.mark_running()
        page = Mock(url=graded.url)
        with patch("v2.runner.open_course", return_value=page), \
             patch("v2.runner.scan_outline", return_value=OutlineSnapshot(self.card.title, (graded,), graded.url)), \
             patch("v2.runner.open_item") as open_item, \
             patch("v2.runner.handle") as handler:
            with self.assertRaisesRegex(RunnerAttention, "await learner input"):
                self.runner._process_course(Mock(), self.card)
        open_item.assert_not_called()
        handler.assert_not_called()
        self.assertEqual(self.store.pending_actions(), [])

    def test_required_priority_skips_practice_and_completed_exam(self):
        self.store.set_meta("required_assessments_first", True)
        def item(title, kind, completed=False):
            return ItemRecord(title=title, kind=kind, completed=completed, url="https://www.coursera.org/learn/example/quiz/"+title, module="M", index=0)
        practice=item("Training", "Practice Assignment")
        passed=item("Passed", "Graded Assignment", True)
        exam=item("Exam", "Graded Assignment")
        lab=item("Lab", "Ungraded Lab")
        self.assertEqual(self.runner._next_coursework(self.cid,[practice,passed,lab,exam]),exam)
        self.assertIsNone(self.runner._next_coursework(self.cid,[practice,passed]))
        self.assertFalse(practice.completed)

    def test_practice_programming_quiz_and_lab_skip_without_completion(self):
        from dataclasses import replace
        self.store.set_meta('required_assessments_first', True)
        mandatory=replace(self.item,kind='Programming Assignment',title='Required lab')
        for kind in ('Practice Programming Assignment','Practice Quiz','Practice Lab'):
            optional=replace(self.item,kind=kind,title='Python Basics with Numpy')
            self.assertEqual(self.runner._next_coursework(self.cid,[optional,mandatory]),mandatory)
            self.assertIsNone(self.runner._next_coursework(self.cid,[optional]))
            self.assertFalse(optional.completed)

    def test_practice_title_does_not_exclude_graded_assignment(self):
        self.store.set_meta("required_assessments_first", True)
        item=ItemRecord(title="Practice Exam",kind="Graded Assignment",completed=False,url="https://www.coursera.org/learn/example/quiz/a",module="M",index=0)
        self.assertEqual(self.runner._next_coursework(self.cid,[item]),item)

    def test_practice_filter_is_explicit_configuration(self):
        item=ItemRecord(title="Quiz",kind="Practice Assignment",completed=False,url="https://www.coursera.org/learn/example/quiz/a",module="M",index=0)
        self.assertEqual(self.runner._next_coursework(self.cid,[item]),item)

    def test_verified_course_is_not_reopened_or_submitted(self):
        self.store.update_course_status(self.cid, "complete", certificate_url="https://www.coursera.org/account/accomplishments/verify/ABC")
        with patch("v2.runner.open_course") as opened, patch("v2.runner.handle") as handler:
            self.runner._process_course(Mock(), self.card)
        opened.assert_not_called()
        handler.assert_not_called()
        self.assertEqual(self.store.get_course(self.cid)["status"], "complete")

    def test_failed_course_does_not_block_next_or_become_complete(self):
        other=CourseCard("Other Course","Get started",0,1,1)
        other_id=course_id(other)
        self.store.upsert_course(other_id,other.title)
        calls=[]
        def process(page,card):
            calls.append(card.title)
            if card==self.card: raise RunnerAttention("Unsupported lab environment")
            self.store.update_course_status(other_id,"complete",certificate_url="https://www.coursera.org/account/accomplishments/verify/XYZ")
            self.runner.completed_courses+=1
        with patch("v2.runner.discover_in_progress",return_value=[self.card,other]) as discover, \
             patch.object(self.runner,"_process_course",side_effect=process):
            summary=self.runner.run(Mock(page=Mock()))
        self.assertEqual(calls,[self.card.title,other.title])
        self.assertEqual(summary.completed_courses,1)
        self.assertEqual(summary.deferred_courses,1)
        self.assertEqual(summary.remaining_courses,1)
        row=self.store.get_course(self.cid)
        self.assertEqual(row["status"],"attention")
        self.assertIsNone(row["certificate_url"])
        self.assertIn("Unsupported lab",self.store.get_meta("course_attention:"+self.cid))
        self.assertEqual(discover.call_count, 2)  # Startup and final refresh, not per-course.

    def test_transient_queue_timeout_retries_then_continues(self):
        from playwright.sync_api import TimeoutError
        page = Mock()
        page.is_closed.return_value = False
        with patch("v2.runner.discover_in_progress", side_effect=[TimeoutError('slow page'), [self.card]]) as read:
            self.assertEqual(self.runner._cards(page), [self.card])
        self.assertEqual(read.call_count, 2)
        self.assertTrue(any(e['code'] == 'QUEUE_RETRY' for e in self.store.recent_events()))

    def test_ambiguous_pending_submission_does_not_block_other_course(self):
        other=CourseCard("Other Course","Resume",20,1,1)
        other_id=course_id(other)
        self.store.upsert_course(other_id,other.title)
        action_id=self.store.begin_action(self.cid,item_id(self.item),"assessment")
        self.store.set_meta(f"submission:{action_id}","submitting")
        with patch("v2.runner.discover_in_progress",return_value=[self.card,other]), \
             patch("v2.runner.open_course",return_value=Mock()), \
             patch("v2.runner.scan_outline",return_value=self._outline(completed=False)), \
             patch.object(self.runner,"_process_course") as process:
            self.runner.run(Mock(page=Mock()))
        self.assertEqual(process.call_count,2)
        self.assertIn((self.cid,self.item.url),self.runner.blocked_items)
        self.assertEqual(process.call_args.args[1],other)
        self.assertEqual(self.store.get_action(action_id)["status"],"pending")

    def test_pre_submit_model_failure_can_be_retried_after_outline_check(self):
        action_id=self.store.begin_action(self.cid,item_id(self.item),"assessment")
        self.store.set_meta(f"submission:{action_id}","not_submitted")
        with patch("v2.runner.open_course",return_value=Mock()), \
             patch("v2.runner.scan_outline",return_value=self._outline(completed=False)):
            self.runner._reconcile(Mock(),[self.card])
        self.assertEqual(self.store.get_action(action_id)["outcome"],"failed")

    def test_final_module_assignment_is_processed_and_verified(self):
        item=ItemRecord("Final test","Graded Assignment",False,"https://www.coursera.org/learn/example/quiz/final","Module 4",9)
        done=ItemRecord(item.title,item.kind,True,item.url,item.module,item.index)
        before=OutlineSnapshot(self.card.title,(item,),item.url)
        after=OutlineSnapshot(self.card.title,(done,),item.url)
        page=Mock(url=item.url)
        with patch("v2.runner.open_course",return_value=page), \
             patch("v2.runner.open_item",return_value=page), \
             patch("v2.runner.scan_outline",side_effect=[before,after,after]), \
             patch("v2.runner.handle",return_value=Mock(message="Passed")) as handler, \
             patch.object(self.runner,"_finish_course") as finish:
            self.runner._process_course(Mock(),self.card)
        self.assertEqual(handler.call_args.args[1],ItemAction.ASSESSMENT)
        self.assertEqual(self.runner.completed_items,1)
        self.assertFalse(self.store.pending_actions())
        finish.assert_called_once()

    def test_video_and_reading_are_excluded_while_quiz_and_lab_are_processed(self):
        from dataclasses import replace
        video = ItemRecord("Lecture", "Video", False, "https://www.coursera.org/learn/demo/lecture/a", "Module 1", 0)
        reading = replace(video, title="Reading", kind="Reading", url=video.url.replace("lecture", "supplement"), index=1)
        quiz = replace(video, title="Quiz", kind="Graded Assignment", url=video.url.replace("lecture", "quiz"), index=2)
        lab = replace(video, title="Lab", kind="Lab", url=video.url.replace("lecture", "ungradedLab"), index=3)
        snapshots = [OutlineSnapshot(self.card.title, items, quiz.url) for items in (
            (video, reading, quiz, lab),
            (video, reading, replace(quiz, completed=True), lab),
            (video, reading, replace(quiz, completed=True), replace(lab, completed=True)),
        )]
        page = Mock(url=quiz.url)
        with patch("v2.runner.open_course", return_value=page), \
             patch("v2.runner.scan_outline", side_effect=snapshots), \
             patch("v2.runner.open_item", return_value=page) as open_item, \
             patch("v2.runner.handle", return_value=Mock(message="done")) as handler, \
             patch.object(self.runner, "_verify_item", return_value=True), \
             patch.object(self.runner, "_finish_course") as finish:
            self.runner._process_course(Mock(), self.card)
        self.assertEqual([c.args[1] for c in open_item.call_args_list], [quiz, lab])
        self.assertEqual([c.args[1] for c in handler.call_args_list], [ItemAction.ASSESSMENT, ItemAction.UNGRADED_LAB])
        self.assertEqual(self.runner.completed_items, 2)
        finish.assert_called_once_with(page, self.card, excluded_items=2)

    def test_pending_video_is_cancelled_without_opening_or_marking_complete(self):
        aid = self.store.begin_action(self.cid, item_id(self.item), "video")
        with patch("v2.runner.open_course") as opened:
            self.runner._reconcile(Mock(), [self.card])
        opened.assert_not_called()
        self.assertEqual(self.store.get_action(aid)["outcome"], "cancelled")
        self.assertEqual(self.runner.completed_items, 0)
        self.assertNotEqual(self.store.get_course(self.cid)["status"], "complete")

    def test_excluded_materials_do_not_fabricate_certificate(self):
        self.runner.certificate_checks = 1
        with patch("v2.runner._verified_certificate_url", return_value=None):
            with self.assertRaisesRegex(RunnerAttention, "2 other incomplete materials"):
                self.runner._finish_course(Mock(), self.card, excluded_items=2)
        self.assertNotEqual(self.store.get_course(self.cid)["status"], "complete")

    def test_handler_failure_preserves_only_possibly_submitted_intent(self):
        item=ItemRecord("Quiz","Quiz",False,"https://www.coursera.org/learn/example/quiz/test","Module 1",0)
        outline=OutlineSnapshot(self.card.title,(item,),item.url)
        for state in ("not_submitted","submitting"):
            self.runner.blocked_items.clear()
            def failing_handler(*args,**kwargs):
                self.store.set_meta(f"submission:{kwargs['action_id']}",state)
                raise HandlerError("test failure")
            with patch("v2.runner.open_course",return_value=Mock(url=item.url)), \
                 patch("v2.runner.open_item",return_value=Mock(url=item.url)), \
                 patch("v2.runner.scan_outline",return_value=outline), \
                 patch("v2.runner.handle",side_effect=failing_handler):
                with self.assertRaises(RunnerAttention): self.runner._process_course(Mock(),self.card)
            self.assertEqual(len(self.store.pending_actions()),int(state=="submitting"))

    def test_discovery_failure_is_reported_as_attention_with_event(self) -> None:
        browser = Mock(page=Mock())
        with patch("v2.runner.discover_in_progress", side_effect=DiscoveryError("queue changed")):
            with self.assertRaisesRegex(RunnerAttention, "queue changed"):
                self.runner.run(browser)
        self.assertEqual(self.store.get_control_state(), "stopped")
        self.assertTrue(any(event["code"] == "ATTENTION" for event in self.store.recent_events()))


class CertificateAndHandlerTests(unittest.TestCase):
    def test_explicit_ungraded_lab_is_not_mistaken_for_graded_work(self) -> None:
        self.assertEqual(decide("Ungraded Lab").action, ItemAction.UNGRADED_LAB)

    def test_certificate_requires_verify_url_and_matching_course(self) -> None:
        valid_url = "https://www.coursera.org/account/accomplishments/verify/ABC123"
        body = "Deep Learning with PyTorch\nCompleted by Bauzhan Dossym"
        self.assertTrue(_certificate_matches(_CertificatePage(valid_url, body), "Deep Learning with PyTorch"))
        self.assertFalse(_certificate_matches(_CertificatePage("https://www.coursera.org/my-learning", body), "Deep Learning with PyTorch"))
        self.assertFalse(_certificate_matches(_CertificatePage(valid_url, body), "Explainable Machine Learning"))

    def test_certificate_does_not_accept_longer_similarly_named_course(self) -> None:
        page = _CertificatePage(
            "https://www.coursera.org/account/accomplishments/verify/ABC123",
            "Introduction to AI for Cybersecurity\nCompleted by Bauzhan Dossym",
        )
        self.assertFalse(_certificate_matches(page, "Introduction to AI"))

    def test_review_required_has_no_browser_handler(self) -> None:
        page = Mock()
        with self.assertRaises(HandlerError):
            handle(page, ItemAction.REVIEW_REQUIRED, lambda: None)
        page.get_by_role.assert_not_called()
        page.locator.assert_not_called()


if __name__ == "__main__":
    unittest.main()

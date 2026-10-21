import unittest
from unittest.mock import Mock
from v2.assessments import _record_reviewed_feedback
from v2.handlers import HandlerError

class FeedbackWorkflowTests(unittest.TestCase):
    def test_incomplete_review_does_not_record_partial_success(self):
        q={'text':'one','kind':'single','options':[]};other={**q,'text':'two'}
        memory=Mock();store=Mock()
        with self.assertRaises(HandlerError):
            _record_reviewed_feedback({'feedback_questions':[{'question':q,'earned':1,'possible':1}]},[(q,{}),(other,{})],memory,store,1)
        memory.record.assert_not_called();store.set_meta.assert_not_called()
    def test_complete_review_keeps_observed_scores(self):
        q={'text':'one','kind':'single','options':[]};memory=Mock();store=Mock()
        _record_reviewed_feedback({'feedback_questions':[{'question':q,'earned':0,'possible':1,'feedback':'Incorrect'}]},[(q,{})],memory,store,1)
        memory.record.assert_called_once_with(q,{},earned=0,possible=1,feedback='Incorrect')
        self.assertEqual(store.set_meta.call_args.args[1]['status'],'scored_questions_matched')

class OldAttemptRecoveryTests(unittest.TestCase):
    def test_restores_observed_selection_without_inventing_grade(self):
        from v2.assessments import _recover_feedback_answers
        q={'text':'Which number?','kind':'single','options':[{'id':'a','text':'2'},{'id':'b','text':'3'}]}
        memory=Mock()
        _recover_feedback_answers([{'question':q,'answer':{'cannot_answer':False,'selected':['b']},'earned':0,'possible':1,'feedback':'Incorrect'}],memory)
        self.assertEqual(memory.record.call_args.kwargs['earned'],0)
        self.assertEqual(memory.record.call_args.args[1]['selected'],['b'])
    def test_missing_selection_blocks_even_with_old_history(self):
        from v2.assessments import _recover_feedback_answers
        memory=Mock()
        with self.assertRaises(HandlerError):
            _recover_feedback_answers([{'question':{'text':'Question','kind':'single','options':[]},'earned':0,'possible':1}],memory)
        memory.record.assert_not_called()

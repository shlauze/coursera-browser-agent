import tempfile
import unittest
from pathlib import Path
from v2.store import Store
from v2.peer import assignment_base, require_source, guard_submission, review_status
from v2.handlers import HandlerError

class PeerSafetyTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.store=Store(Path(self.tmp.name)/'state.sqlite3')
        self.base='https://www.coursera.org/learn/course/peer/id/essay'
    def test_route_identity_excludes_other_courses_and_external_hosts(self):
        self.assertEqual(assignment_base(self.base+'/submit'),self.base)
        self.assertEqual(assignment_base(self.base+'/give-feedback'),self.base)
        for url in ['https://example.org/learn/course/peer/id/essay',self.base+'/unknown']:
            with self.assertRaises(HandlerError):assignment_base(url)
    def test_uncertain_or_sent_submission_cannot_repeat_across_action_ids(self):
        for status in ['submitting','submitted','pending','complete']:
            self.store.set_meta('peer_state:'+self.base,{'status':status,'action_id':1})
            with self.assertRaisesRegex(HandlerError,'repeat'):guard_submission(self.store,self.base)
    def test_draft_does_not_claim_completion(self):
        self.store.set_meta('peer_state:'+self.base,{'status':'draft_verified'})
        guard_submission(self.store,self.base)
        self.assertEqual(self.store.get_meta('peer_state:'+self.base)['status'],'draft_verified')
    def test_missing_or_generated_essay_cannot_confirm_authorship(self):
        for value in [None,{}, {'authorship':'ai'}, {'authorship':'learner','prompt':'wrong'}]:
            with self.assertRaises(HandlerError):require_source(value,'exact prompt')
        source={'authorship':'learner','prompt':'exact prompt','title':'A title','text':' '.join(['word']*30)}
        self.assertEqual(require_source(source,'exact prompt'),source)
    def test_locked_reviews_preserve_actual_count_instead_of_assuming_three(self):
        for count in (2, 3):
            state = review_status(f'Reviews {count} left to complete Submit your project before reviewing classmates.')
            self.assertEqual(state, {'state': 'submission_required', 'remaining': count})

    def test_reviews_locked_until_submission_not_fabricated_as_done(self):
        self.assertEqual(review_status('Reviews 3 left to complete Submit your project before reviewing classmates.')['state'],'submission_required')
        self.assertEqual(review_status('Reviews 3 left to complete')['remaining'],3)
        self.assertEqual(review_status('Reviews')['state'],'unknown')

if __name__=='__main__':unittest.main()

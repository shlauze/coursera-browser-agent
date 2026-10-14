"""Exercise both Coursera pagination layouts against the actual page scripts."""
import json
import subprocess
import unittest

import my_learning as learning


NODE = r"""
const vm = require('vm');
const fs = require('fs');
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
let clicks = 0;
function element(text, attrs={}) {
  return {
    innerText:text, textContent:text, parentElement:null, disabled:false,
    getAttribute:key=>attrs[key] ?? null,
    getClientRects:()=>[{}],
    getBoundingClientRect:()=>({width:100,height:20}),
    click:()=>{clicks++},
  };
}
const next = element('', {'aria-label':'Go to next page'});
const statuses = (input.statuses || []).map(text=>element(text));
const numbered = input.numbered ? element('', {'aria-label':'Page number '+input.numbered}) : null;
const tab = element(input.completed ? 'Completed' : 'In Progress');
const body = element(input.completed ? 'No completed courses' : 'No courses in progress');
const document = {
  readyState:'complete', body, documentElement:body,
  querySelector:selector=>selector==='main' ? body : null,
  querySelectorAll:selector=>{
    if(selector==='button[aria-label],a[aria-label]') return numbered ? [numbered,next] : [next];
    if(selector==='[data-testid="mobile-pagination"] [role="status"]') return statuses;
    if(selector==='[role="tab"][aria-selected="true"]') return [tab];
    return [];
  },
};
const context = {document, location:{href:'https://www.coursera.org/my-learning?myLearningTab='+
  (input.completed ? 'COMPLETED' : 'IN_PROGRESS')}, URL,
  getComputedStyle:()=>({display:'block',visibility:'visible',opacity:'1'})};
const fn = vm.runInNewContext(input.script, context);
const inspected = fn({action:'inspect', course_id:'unused', titles:[]});
const clicked = input.click ? fn({action:'next_page', signature:inspected.signature,
  course_id:'unused',titles:[]}) : null;
process.stdout.write(JSON.stringify({inspected, clicked, clicks}));
"""


def inspect(*, status=None, numbered=None, completed=False, click=False):
    payload = {
        'script': learning.COMPLETED_PAGE_JS if completed else learning.PAGE_JS,
        'statuses': status if isinstance(status, list) else ([status] if status else []),
        'numbered': numbered, 'completed': completed, 'click': click,
    }
    process = subprocess.run(['node', '-e', NODE], input=json.dumps(payload),
                             text=True, capture_output=True, check=True)
    return json.loads(process.stdout)


class PaginationScriptTests(unittest.TestCase):
    def test_compact_pagination_recognized_and_next_clicked(self):
        for completed in (False, True):
            with self.subTest(completed=completed):
                result = inspect(status='Page 1 of 6', completed=completed, click=True)
                self.assertEqual(result['inspected']['page'], 1)
                self.assertTrue(result['inspected']['has_next'])
                self.assertTrue(result['clicked']['clicked'])
                self.assertEqual(result['clicked']['previous_page'], 1)
                self.assertEqual(result['clicks'], 1)

    def test_numbered_pagination_remains_supported(self):
        self.assertEqual(inspect(numbered=2)['inspected']['page'], 2)

    def test_conflicting_or_malformed_pagination_is_not_guessed(self):
        for kwargs in (
            {'status':['Page 1 of 6', 'Page 2 of 6']},
            {'status':'Page 1 of 6', 'numbered':2},
            {'status':'Page 7 of 6'},
            {'status':'Page 1 of 1000'},
        ):
            with self.subTest(kwargs=kwargs):
                self.assertIsNone(inspect(**kwargs)['inspected']['page'])


if __name__ == '__main__':
    unittest.main()

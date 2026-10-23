"""Exercise the real bridge in a browser with deterministic network failures."""
import unittest
from playwright.sync_api import sync_playwright, Error
from v2.notebooks import BRIDGE


class NotebookReadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.p = sync_playwright().start()
        cls.browser = cls.p.chromium.launch(channel='chrome', headless=True)

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.p.stop()

    def setUp(self):
        self.context = self.browser.new_context()
        self.addCleanup(self.context.close)
        self.page = self.context.new_page()
        self.page.route('**/*', lambda route: route.fulfill(body='<html><body>Fixture</body></html>', content_type='text/html'))
        self.page.goto('https://notebook.test/')
        self.page.evaluate('''() => {
            globalThis.reads=0; globalThis.saves=0; globalThis.failures=2;
            globalThis.failureKind='timeout'; globalThis.saveFails=false;
            const document={cells:[{cell_type:'code',source:'x=1',metadata:{}}]};
            globalThis.Jupyter={notebook:{notebook_path:'lab.ipynb',base_url:'/',
                get_cells:()=>[{}],toJSON:()=>document,
                save_notebook:()=>{saves++; return saveFails ? Promise.reject(new Error('save interrupted')) : Promise.resolve();},
                kernel:{id:'kernel1',kernel_url:'/api/kernels/kernel1',is_connected:()=>true}}};
            globalThis.fetch=async(url,options)=>{
                if(options.method!=='GET') throw new Error('Only GET may be retried');
                reads++;
                if(reads<=failures) {
                    if(failureKind==='timeout') throw new DOMException('signal timed out','TimeoutError');
                    return new Response('',{status:failureKind==='denied'?403:503});
                }
                const data=String(url).includes('/api/contents/')?{type:'notebook',content:document}:{id:'kernel1',execution_state:'idle'};
                return new Response(JSON.stringify(data),{status:200});
            };
        }''')

    def test_snapshot_recovers_after_two_read_timeouts(self):
        result=self.page.evaluate(BRIDGE,{'action':'snapshot'})
        self.assertEqual(result['kernel'],'idle')
        self.assertEqual(self.page.evaluate('reads'),3)
        self.assertEqual(self.page.evaluate('saves'),0)

    def test_busy_run_reports_explicit_non_execution_before_touching_cell(self):
        self.page.evaluate('''() => {
            globalThis.executions = 0;
            Jupyter.notebook.get_cells = () => [{execute: () => executions++}];
            globalThis.fetch = async () => new Response(JSON.stringify({id:'kernel1',execution_state:'busy'}),{status:200});
        }''')
        result = self.page.evaluate(BRIDGE, {'action':'run','index':0,'token':'fixture'})
        self.assertIs(result['execution_started'], False)
        self.assertEqual(self.page.evaluate('executions'), 0)

    def test_save_verification_retries_get_but_saves_once(self):
        self.page.evaluate("failureKind='server'")
        self.assertEqual(self.page.evaluate(BRIDGE,{'action':'save'}),{'ok':True})
        self.assertEqual(self.page.evaluate('reads'),3)
        self.assertEqual(self.page.evaluate('saves'),1)

    def test_read_retry_budget_is_bounded(self):
        self.page.evaluate('failures=10')
        with self.assertRaises(Error):
            self.page.evaluate(BRIDGE,{'action':'snapshot'})
        self.assertEqual(self.page.evaluate('reads'),3)

    def test_permission_failure_is_not_retried(self):
        self.page.evaluate("failureKind='denied'")
        with self.assertRaises(Error):
            self.page.evaluate(BRIDGE,{'action':'snapshot'})
        self.assertEqual(self.page.evaluate('reads'),1)

    def test_save_failure_does_not_replay_write(self):
        self.page.evaluate('saveFails=true')
        with self.assertRaises(Error):
            self.page.evaluate(BRIDGE,{'action':'save'})
        self.assertEqual(self.page.evaluate('saves'),1)
        self.assertEqual(self.page.evaluate('reads'),0)

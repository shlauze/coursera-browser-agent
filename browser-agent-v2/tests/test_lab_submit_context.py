import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from playwright.sync_api import sync_playwright
from v2.notebooks import handle_programming_lab
from v2.groq_client import GroqClient, compact_notebook_context
from v2.store import Store

class LabFixTests(unittest.TestCase):
 def test_submission_button_inside_iframe(self):
  with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
   store=Store(Path(tmp)/'state.db');browser=p.chromium.launch(channel="chrome",headless=True)
   try:
    page=browser.new_page();page.set_content('<iframe></iframe>')
    frame=page.frames[1];frame.set_content("<button onclick=\"this.insertAdjacentHTML('afterend','<p>Assignment submitted</p>');this.disabled=true\">Submit Assignment</button>")
    executor=Mock();executor.execute.return_value={'source_sha256':hashlib.sha256(b'same').hexdigest()};executor.call.return_value={'signature':'same'}
    with patch('v2.notebooks.wait_for_notebook',return_value=(page,frame)),patch('v2.notebooks.NotebookExecutor',return_value=executor):
     handle_programming_lab(page,lambda:None,groq=Mock(),store=store,action_id=1)
    self.assertEqual(store.get_meta('submission:1'),'submitted')
   finally:browser.close()
 def test_compaction_preserves_code_instructions_and_traceback(self):
  original={'notebook':[{'index':0,'type':'markdown','source':'unrelated'}, {'index':1,'type':'code','source':'import numpy'}, {'index':2,'type':'markdown','source':'required instructions'}, {'index':3,'type':'code','source':'def answer(): pass'}], 'editable_cells':[3], 'failure':{'index':3,'reply':{'traceback':['NameError']},'outputs':[{'image/png':'large'}]}}
  result=compact_notebook_context(original)
  self.assertEqual([x['index'] for x in result['notebook']],[1,2,3])
  self.assertEqual(result['failure']['reply'],original['failure']['reply'])
  self.assertIn('outputs',original['failure'])
 def test_413_retries_compact_once(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp)/'state.db');payloads=[]
   def transport(key,payload):
    payloads.append(json.loads(json.dumps(payload)))
    if len(payloads)==1:return 413,{},{}
    return 200,{}, {'choices':[{'finish_reason':'stop','message':{'content':json.dumps({'cannot_complete':False,'index':1,'code':'x=1'})}}]}
   client=GroqClient(store,key_loader=lambda:'test',transport=transport)
   client.notebook_patch({'notebook':[{'index':0,'type':'markdown','source':'unrelated'}, {'index':1,'type':'code','source':'x=0'}], 'editable_cells':[]})
   self.assertEqual(len(payloads),2)
   self.assertEqual(payloads[0]['max_completion_tokens'],2048)
   self.assertLess(len(payloads[1]['messages'][-1]['content']),len(payloads[0]['messages'][-1]['content'])+300)

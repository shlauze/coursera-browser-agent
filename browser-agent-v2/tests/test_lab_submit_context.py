import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from playwright.sync_api import sync_playwright
from v2.notebooks import handle_programming_lab
from v2.groq_client import GroqClient, GroqError, TruncatedResponse, compact_notebook_context, focused_notebook_context
from v2.handlers import HandlerError
from v2.store import Store

class LabFixTests(unittest.TestCase):
 def test_unrelated_empty_loop_suites_do_not_expand_context(self):
  empty='def unrelated(values):\n for value in values:\n # YOUR CODE STARTS HERE\n\n # YOUR CODE ENDS HERE\n return values'
  context={'notebook':[{'index':0,'type':'code','source':'import math'},
    {'index':1,'type':'markdown','source':'Instructions for target'},
    {'index':2,'type':'code','source':'def target(x):\n return math.floor(x)'},
    {'index':3,'type':'markdown','source':'Other exercise instructions'},
    {'index':4,'type':'code','source':empty}], 'editable_cells':[2,4],'target':2}
  result=focused_notebook_context(context)
  self.assertEqual([c['index'] for c in result['notebook']],[0,1,2])
  self.assertEqual(context['notebook'][4]['source'],empty)
  context['target']=4
  selected=focused_notebook_context(context)
  self.assertEqual(next(c['source'] for c in selected['notebook'] if c['index']==4),empty)
 def test_referenced_empty_helper_remains_in_context(self):
  helper='def helper(values):\n for value in values:\n  # YOUR CODE STARTS HERE\n\n  # YOUR CODE ENDS HERE\n return values'
  context={'notebook':[{'index':0,'type':'code','source':helper},
                      {'index':1,'type':'code','source':'def target(x):\n return helper(x)'}],
           'editable_cells':[0,1],'target':1}
  self.assertEqual([c['index'] for c in focused_notebook_context(context)['notebook']],[0,1])
 def test_truncated_lab_response_grows_once_and_remembers_budget(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp)/'state.db');budgets=[]
   answer={'cannot_complete':False,'index':1,'code':'x=1'}
   def transport(key,payload):
    budgets.append(payload['max_completion_tokens'])
    reason='length' if len(budgets)==1 else 'stop'
    return 200,{}, {'choices':[{'finish_reason':reason,'message':{'content':json.dumps(answer)}}]}
   context={'notebook':[],'editable_cells':[1]}
   client=GroqClient(store,key_loader=lambda:'test',transport=transport)
   self.assertEqual(client.notebook_patch(context),answer)
   self.assertEqual(budgets,[2048,4096])
   restarted=GroqClient(Store(store.path),key_loader=lambda:'test',transport=transport)
   self.assertEqual(restarted.notebook_patch(context),answer)
   self.assertEqual(budgets,[2048,4096,4096])
 def test_repeated_truncation_has_bounded_retry(self):
  with tempfile.TemporaryDirectory() as tmp:
   budgets=[]
   def transport(key,payload):
    budgets.append(payload['max_completion_tokens'])
    return 200,{}, {'choices':[{'finish_reason':'length','message':{'content':'{"cannot_complete":false'}}]}
   with self.assertRaises(TruncatedResponse):
    GroqClient(Store(Path(tmp)/'state.db'),key_loader=lambda:'test',transport=transport).notebook_patch({})
   self.assertEqual(budgets,[2048,4096])
 def test_other_incomplete_reasons_are_not_retried(self):
  with tempfile.TemporaryDirectory() as tmp:
   transport=Mock(return_value=(200,{}, {'choices':[{'finish_reason':'content_filter','message':{'content':'{}'}}]}))
   with self.assertRaises(GroqError):
    GroqClient(Store(Path(tmp)/'state.db'),key_loader=lambda:'test',transport=transport).notebook_patch({})
   self.assertEqual(transport.call_count,1)
 def test_region_request_uses_structured_region_schema(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp)/'state.db');payloads=[]
   response={'cannot_complete':False,'index':1,'regions':[{'number':0,'code':'x=1'}]}
   def transport(key,payload):
    payloads.append(payload)
    return 200,{}, {'choices':[{'finish_reason':'stop','message':{'content':json.dumps(response)}}]}
   context={'notebook':[{'index':1,'type':'code','source':'# YOUR CODE STARTS HERE\n\n# YOUR CODE ENDS HERE'}],
            'editable_cells':[1],'target':1,'solution_regions':{'1':[{'number':0,'source':'\n'}]}}
   result=GroqClient(store,key_loader=lambda:'test',transport=transport).notebook_patch(context)
   self.assertEqual(result,response)
   properties=payloads[0]['response_format']['json_schema']['schema']['properties']
   self.assertIn('regions',properties)
   self.assertNotIn('code',properties)
 def test_submission_button_inside_iframe(self):
  self.check_submission_message('Assignment submitted')
 def test_workspace_success_inside_iframe_is_not_automatically_a_grade(self):
  self.check_submission_message('Programming workspace submission succeeded')
 def check_submission_message(self, message):
  with tempfile.TemporaryDirectory() as tmp, sync_playwright() as p:
   store=Store(Path(tmp)/'state.db');browser=p.chromium.launch(channel="chrome",headless=True)
   try:
    page=browser.new_page();page.set_content('<iframe></iframe>')
    frame=page.frames[1];frame.set_content("<button onclick=\"this.insertAdjacentHTML('afterend','<p>"+message+"</p>');this.disabled=true\">Submit Assignment</button>")
    executor=Mock();executor.execute.return_value={'source_sha256':hashlib.sha256(b'same').hexdigest()};executor.call.return_value={'signature':'same'}
    with patch('v2.notebooks.wait_for_notebook',return_value=(page,frame)),patch('v2.notebooks.NotebookExecutor',return_value=executor):
     handle_programming_lab(page,lambda:None,groq=Mock(),store=store,action_id=1)
    self.assertEqual(store.get_meta('submission:1'),'submitted')
   finally:browser.close()
 def test_uncertain_submission_never_executes_or_submits_again(self):
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(Path(tmp)/'state.db');store.set_meta('submission:1','submitting')
   page=Mock()
   with self.assertRaisesRegex(HandlerError,'reconciliation'):
    handle_programming_lab(page,lambda:None,groq=Mock(),store=store,action_id=1)
   self.assertEqual(page.mock_calls,[])
 def test_focused_repair_preserves_transitive_dependencies_and_instructions(self):
  original={'notebook':[
   {'index':0,'type':'code','source':'import numpy as np'},
   {'index':1,'type':'markdown','source':'Required math and shape'},
   {'index':2,'type':'code','source':'def helper(x):\n return np.sum(x)'},
   {'index':3,'type':'markdown','source':'Required exercise instructions'},
   {'index':4,'type':'code','source':'def answer(x):\n return helper(x)'},
   {'index':5,'type':'code','source':'assert answer([1,2]) == 3'},
   {'index':6,'type':'markdown','source':'Unrelated exercise'},
   {'index':7,'type':'code','source':'def unrelated():\n return 42'}],
   'editable_cells':[2,4,7], 'failure':{'index':5,'reply':{'traceback':['\x1b[31mNameError\x1b[0m']},'outputs':['duplicate']}}
  before=json.dumps(original)
  result=focused_notebook_context(original)
  self.assertEqual([c['index'] for c in result['notebook']],[0,1,2,3,4,5])
  self.assertEqual(result['editable_cells'],[2,4])
  self.assertEqual(result['failure']['reply']['traceback'],['NameError'])
  self.assertEqual(json.dumps(original),before)
 def test_unknown_dependency_does_not_drop_editable_cells(self):
  context={'notebook':[{'index':0,'type':'code','source':'def answer(): pass'},
   {'index':1,'type':'code','source':'eval("answer()")'}], 'editable_cells':[0], 'failure':{'index':1}}
  self.assertEqual(focused_notebook_context(context)['editable_cells'],[0])
 def test_function_parameters_do_not_pull_unrelated_training_cells(self):
  context={'notebook':[
   {'index':0,'type':'code','source':'parameters = train_everything()'},
   {'index':1,'type':'code','source':'def answer(parameters):\n cost = parameters[0]\n return cost'},
   {'index':2,'type':'code','source':'parameters = [3]\nassert answer(parameters) == 3'}],
   'editable_cells':[1], 'failure':{'index':2}}
  self.assertEqual([c['index'] for c in focused_notebook_context(context)['notebook']],[1,2])
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

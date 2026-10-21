import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from v2.store import Store
from v2.groq_client import GroqClient, AnswerMemory, GroqError, question_key

class DraftTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
  self.path=Path(self.tmp.name)/"state.sqlite3";self.store=Store(self.path)
  self.q={"text":"Choose X", "kind":"single", "options":[{"id":"a","text":"X"},{"id":"b","text":"Y"}]}
  self.transport=Mock(return_value=(200,{}, {"choices":[{"finish_reason":"stop","message":{"content":json.dumps({"cannot_answer":False,"selected":["a"]})}}]}))
 def client(self,store=None,**kw):
  return GroqClient(store or self.store,key_loader=lambda:"test",transport=self.transport,**kw)
 def test_restart_uses_durable_draft_and_remaps_ids_without_request(self):
  self.client().answer(self.q)
  q=copy.deepcopy(self.q);q["options"]=[{"id":"new1","text":"Y"},{"id":"new2","text":"X"}]
  store=Store(self.path)
  self.assertEqual(self.client(store).answer(q)["selected"],["new2"])
  self.assertEqual(self.transport.call_count,1)
  self.assertEqual(AnswerMemory(store).history(q),[])
  self.assertIsNone(AnswerMemory(store).confirmed(q))
 def test_new_feedback_invalidates_draft_and_blocks_rejected_answer(self):
  self.client().answer(self.q)
  AnswerMemory(self.store).record(self.q,{"cannot_answer":False,"selected":["a"]},earned=0,possible=1)
  with self.assertRaisesRegex(GroqError,"submission blocked"):self.client().answer(self.q)
  self.assertEqual(self.transport.call_count,2)
 def test_source_change_invalidates_draft(self):
  self.client().answer(self.q)
  self.store.set_meta("answer_guidance:"+question_key(self.q),{"source_url":"https://example.test/source","explanation":"New course evidence"})
  self.client().answer(self.q);self.assertEqual(self.transport.call_count,2)
 def test_changed_options_are_not_reused(self):
  self.client().answer(self.q)
  q=copy.deepcopy(self.q);q["options"][1]["text"]="Z"
  self.client().answer(q);self.assertEqual(self.transport.call_count,2)
 def test_model_change_invalidates_draft(self):
  self.client(model="model-a").answer(self.q);self.client(model="model-b").answer(self.q)
  self.assertEqual(self.transport.call_count,2)
 def test_invalid_response_is_not_saved(self):
  self.transport.return_value=(200,{}, {"choices":[{"finish_reason":"stop","message":{"content":'{"cannot_answer":true}'}}]})
  with self.assertRaises(GroqError):self.client().answer(self.q)
  self.assertIsNone(self.store.get_meta("answer_draft:"+question_key(self.q)))
 def test_save_failure_prevents_return_to_form_filler(self):
  with patch.object(self.store,"set_meta",side_effect=OSError("disk unavailable")):
   with self.assertRaises(OSError):self.client().answer(self.q)
 def test_stop_is_honored_even_with_draft(self):
  self.client().answer(self.q)
  client=self.client();client.checkpoint=Mock(side_effect=RuntimeError("stop"))
  with self.assertRaisesRegex(RuntimeError,"stop"):client.answer(self.q)
  self.assertEqual(self.transport.call_count,1)
 def test_corrupt_cache_is_not_used(self):
  self.client().answer(self.q)
  key="answer_draft:"+question_key(self.q);row=self.store.get_meta(key);row["selected_texts"]=["missing"];self.store.set_meta(key,row)
  self.client().answer(self.q);self.assertEqual(self.transport.call_count,2)
 def test_text_draft_revalidated_against_new_length_limit(self):
  q={"text":"Explain", "kind":"text", "max_length":20}
  self.transport.return_value=(200,{}, {"choices":[{"finish_reason":"stop","message":{"content":'{"cannot_answer":false,"text":"some text"}'}}]})
  self.client().answer(q);self.client(Store(self.path)).answer(q);self.assertEqual(self.transport.call_count,1)
  q["max_length"]=2
  with self.assertRaises(GroqError):self.client().answer(q)
  self.assertEqual(self.transport.call_count,2)

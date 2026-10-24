import copy
import json
from pathlib import Path
import tempfile
import unittest

from v2.groq_client import AnswerMemory, GroqClient, GroqError, RequestTooLarge, question_key, too_large
from v2.store import Store


def success(answer):
    return 200, {}, {"choices":[{"finish_reason":"stop", "message":{"content":json.dumps(answer)}}]}


class GroqTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = Store(Path(self.tmp.name)/"test.sqlite3")
        self.question = {"text":"Which number is even?", "kind":"single", "options":[{"id":"a","text":"2"},{"id":"b","text":"3"}]}

    def client(self, transport, **kwargs):
        kwargs.setdefault("max_wait", 180)
        return GroqClient(self.store, key_loader=lambda:"test-key", transport=transport, **kwargs)

    def test_model_cannot_repeat_observed_failed_choice_after_option_reordering(self):
        memory=AnswerMemory(self.store)
        memory.record(self.question,{"cannot_answer":False,"selected":["b"]},earned=0,possible=1,feedback="Incorrect")
        changed=copy.deepcopy(self.question)
        changed['options']=[{"id":"new0","text":"3"},{"id":"new1","text":"2"}]
        client=self.client(lambda *args:success({"cannot_answer":False,"selected":["new0"]}))
        with self.assertRaisesRegex(GroqError,'submission blocked'):client.answer(changed)
        self.assertFalse(memory.rejected(changed,{"cannot_answer":False,"selected":["new1"]}))

    def test_reviewed_context_is_question_scoped_and_not_forged_score(self):
        context={'source_url':'https://www.coursera.org/learn/example/lecture/id/title','explanation':'Read the actual premises, not their truth.'}
        self.store.set_meta('answer_guidance:'+question_key(self.question),context)
        seen=[]
        def transport(key,payload):
            seen.append(json.loads(payload['messages'][-1]['content']))
            return success({'cannot_answer':False,'selected':['a']})
        self.client(transport).answer(self.question)
        self.assertEqual(seen[0]['reviewed_course_context'],context)
        self.assertIsNone(AnswerMemory(self.store).confirmed(self.question))

    def test_production_waits_on_same_question_beyond_old_budget(self):
        calls=[]; sleeps=[]
        now = [100.0]
        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        def transport(*args):
            calls.append(1)
            if len(calls) < 4:
                return 429, {"retry-after":"120"}, {"error":{"message":"Tokens per day (TPD): Limit 200000 Used 199900 Requested 500"}}
            return success({"cannot_answer":False,"selected":["a"]})
        self.client(transport, max_wait=None, sleep=sleep, clock=lambda: now[0]).answer(self.question)
        self.assertEqual(len(calls),4)
        self.assertEqual(sum(sleeps),360)
        self.assertEqual(self.store.get_meta("groq_rate_limit")["metric"],"TPD")
        self.assertEqual(self.store.get_meta("groq_retry_until"),0)

    def test_cooldown_survives_new_client_and_stop(self):
        self.store.set_meta("groq_retry_until",110)
        calls=[]; sleeps=[]
        def transport(*args):
            calls.append(sum(sleeps))
            return success({"cannot_answer":False,"selected":["a"]})
        self.client(transport, clock=lambda:100, sleep=sleeps.append).answer(self.question)
        self.assertEqual(calls,[10])
        self.store.set_meta("groq_retry_until",110)
        client=self.client(transport, clock=lambda:100, sleep=sleeps.append)
        def stop(): raise RuntimeError("stop")
        client.checkpoint=stop
        with self.assertRaisesRegex(RuntimeError,"stop"):
            client.answer(self.question)
        self.assertEqual(calls,[10])
        self.assertEqual(self.store.get_meta("groq_retry_until"),110)

    def test_strict_schema_and_safe_400_diagnostics(self):
        seen=[]
        def transport(key,payload):
            seen.append(payload)
            return 400, {}, {"error":{"code":"json_validate_failed", "failed_generation":"PRIVATE"}}
        with self.assertRaisesRegex(GroqError, r"HTTP 400 \(json_validate_failed\)"):
            self.client(transport).answer(self.question)
        fmt=seen[0]["response_format"]
        self.assertEqual(fmt["type"],"json_schema")
        self.assertTrue(fmt["json_schema"]["strict"])
        self.assertFalse(fmt["json_schema"]["schema"]["additionalProperties"])
        self.assertEqual(len(seen),1)

    def test_case_sensitive_math_memory(self):
        q={"text":"Choose variable", "kind":"single", "options":[{"id":"a","text":"x"},{"id":"b","text":"X"}]}
        memory=AnswerMemory(self.store)
        memory.record(q,{"cannot_answer":False,"selected":["b"]},earned=1,possible=1)
        self.assertEqual(memory.confirmed(q)["selected"],["b"])
        other=copy.deepcopy(q);other["text"]="choose variable"
        self.assertNotEqual(question_key(q),question_key(other))

    def test_notebook_json_rejection_retries_once_with_local_schema_validation(self):
        for regions in (False, True):
            context = {'editable_cells': [2], 'target': 2, 'notebook': [{'source': '# YOUR CODE HERE'}]}
            answer = {'cannot_complete': False, 'index': 2, 'code': 'x = 1'}
            if regions:
                context['solution_regions'] = {'2': [{'number': 0, 'source': ''}]}
                answer = {'cannot_complete': False, 'index': 2, 'regions': [{'number': 0, 'code': 'x = 1'}]}
            seen = []
            def transport(key, payload):
                seen.append(copy.deepcopy(payload))
                if len(seen) == 1:
                    return 400, {}, {'error': {'code': 'json_validate_failed', 'failed_generation': 'PRIVATE'}}
                return success(answer)
            self.assertEqual(self.client(transport).notebook_patch(context), answer)
            self.assertEqual(len(seen), 2)
            self.assertEqual(seen[1]['response_format'], {'type': 'json_object'})
            self.assertEqual(seen[0]['messages'], seen[1]['messages'][:-1])
            self.assertIn('exactly these fields', seen[1]['messages'][-1]['content'])
            self.assertNotIn('PRIVATE', json.dumps(seen))

    def test_notebook_repeated_json_rejection_is_bounded_and_private(self):
        seen = []
        def transport(key, payload):
            seen.append(1)
            return 400, {}, {'error': {'code': 'json_validate_failed', 'failed_generation': 'PRIVATE'}}
        with self.assertRaisesRegex(GroqError, 'json_validate_failed') as error:
            self.client(transport).notebook_patch({'editable_cells': [2], 'target': 2, 'notebook': []})
        self.assertEqual(len(seen), 2)
        self.assertNotIn('PRIVATE', str(error.exception))

    def test_notebook_other_400_does_not_retry(self):
        seen = []
        def transport(key, payload):
            seen.append(1)
            return 400, {}, {'error': {'code': 'invalid_request_error'}}
        with self.assertRaisesRegex(GroqError, 'invalid_request_error'):
            self.client(transport).notebook_patch({'editable_cells': [2], 'target': 2, 'notebook': []})
        self.assertEqual(len(seen), 1)

    def test_notebook_patch_uses_json_and_reports_inability(self):
        context = {'editable_cells': [2], 'target': 2, 'notebook': [{'source': '# YOUR CODE HERE'}]}
        seen = []
        def transport(key, payload):
            seen.append(payload)
            return success({'cannot_complete': False, 'index': 2, 'code': 'answer = 42'})
        self.assertEqual(self.client(transport).notebook_patch(context)['index'], 2)
        self.assertEqual(json.loads(seen[0]['messages'][1]['content']), context)
        with self.assertRaises(GroqError):
            self.client(lambda *args: success({'cannot_complete': True})).notebook_patch(context)

    def test_one_question_per_request_and_size_reduction_keeps_question(self):
        memory=AnswerMemory(self.store)
        for i in range(5):
            memory.record(self.question, {"cannot_answer":False,"selected":["b"]}, earned=0,possible=1,feedback="f"*2000)
        seen=[]
        def transport(key,payload):
            data=json.loads(payload["messages"][1]["content"]); seen.append(data)
            if len(seen)<3:
                return 413, {}, {}
            return success({"cannot_answer":False,"selected":["a"]})
        with self.assertRaisesRegex(RequestTooLarge, "Feedback context cannot fit"):
            self.client(transport).answer(self.question)
        self.assertEqual(len(seen),2)
        self.assertTrue(all(x["question"]==self.question for x in seen))
        self.assertTrue(all(x["previous_observations"] for x in seen))
        self.assertGreater(len(json.dumps(seen[0])),len(json.dumps(seen[1])))

    def test_429_waits_at_least_retry_after_and_checkpoints(self):
        calls=[]; sleeps=[]; checkpoints=[]
        now = [100.0]
        def sleep(seconds):
            sleeps.append(seconds)
            now[0] += seconds
        def transport(key,payload):
            calls.append(1)
            if len(calls)==1:
                return 429,{"Retry-After":"3","X-RateLimit-Remaining-Tokens":"0","Authorization":"secret"},{}
            return success({"cannot_answer":False,"selected":["a"]})
        client=self.client(transport,sleep=sleep,clock=lambda: now[0])
        client.checkpoint=lambda:checkpoints.append(1)
        client.answer(self.question)
        self.assertEqual(sum(sleeps),3)
        self.assertGreaterEqual(len(checkpoints),3)
        self.assertNotIn("authorization",self.store.get_meta("groq_limits"))

    def test_excessive_retry_after_defers_without_shortened_retry(self):
        calls=[]; sleeps=[]
        def transport(key,payload):
            calls.append(1); return 429,{"retry-after":"3600"},{}
        with self.assertRaisesRegex(GroqError,"budget"):
            self.client(transport,sleep=sleeps.append).answer(self.question)
        self.assertEqual(len(calls),1); self.assertFalse(sleeps)

    def test_immutable_size_is_different_from_used_plus_requested(self):
        self.assertFalse(too_large(429,{"error":{"message":"Limit 8000 Used 7900 Requested 200"}}))
        self.assertTrue(too_large(429,{"error":{"message":"Limit 8000 Requested 9000"}}))
        with self.assertRaises(RequestTooLarge):
            self.client(lambda *args:(413,{},{})).answer(self.question)

    def test_only_observed_full_score_is_reused_with_reordered_ids(self):
        memory=AnswerMemory(self.store)
        memory.record(self.question,{"cannot_answer":False,"selected":["a"]},earned=1,possible=1)
        reordered=copy.deepcopy(self.question)
        reordered["options"]=[{"id":"new0","text":"3"},{"id":"new1","text":"2"}]
        answer=self.client(lambda *args:self.fail("Cache should avoid API")).answer(reordered)
        self.assertEqual(answer["selected"],["new1"])
        memory.record(self.question,{"cannot_answer":False,"selected":["a"]},earned=0,possible=1)
        self.assertIsNone(memory.confirmed(self.question))

    def test_unknown_score_and_changed_options_never_reused(self):
        memory=AnswerMemory(self.store)
        memory.record(self.question,{"cannot_answer":False,"selected":["a"]})
        self.assertIsNone(memory.confirmed(self.question))
        altered=copy.deepcopy(self.question); altered["options"][1]["text"]="4"
        self.assertNotEqual(question_key(self.question),question_key(altered))

    def test_auth_error_and_invalid_choice_do_not_repeat(self):
        for response in [(401,{},{}),success({"cannot_answer":False,"selected":["unknown"]})]:
            calls=[]
            def transport(*args):
                calls.append(1); return response
            with self.assertRaises(GroqError): self.client(transport).answer(self.question)
            self.assertEqual(len(calls),1)

    def test_truncated_answer_is_not_used(self):
        response=success({"cannot_answer":False,"selected":["a"]})
        response[2]["choices"][0]["finish_reason"]="length"
        with self.assertRaisesRegex(GroqError,"truncated"):
            self.client(lambda *args:response).answer(self.question)


if __name__ == "__main__": unittest.main()

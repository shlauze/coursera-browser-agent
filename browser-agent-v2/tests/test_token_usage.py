import json
import tempfile
import unittest
from pathlib import Path
from datetime import datetime, timezone
from v2.store import Store
from v2.groq_client import GroqClient, GroqError
from v2.token_usage import record_usage, usage_summary, usage_html
from v2.dashboard import Dashboard
from v2.control import Control

class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name)/"state.db"
        self.s = Store(self.path)

    def test_day_boundary_and_restart(self):
        for hour in (18,19):
            record_usage(self.s,{"usage":{"prompt_tokens":10,"completion_tokens":2,"total_tokens":12}},200,"model","c1",now=datetime(2026,10,21,hour,59,tzinfo=timezone.utc))
        result=usage_summary(Store(self.path))
        self.assertEqual(result["totals"]["total_tokens"],24)
        self.assertEqual([x["day"] for x in result["by_day_course"]],["2026-10-22","2026-10-21"])
        self.assertEqual(result["by_day_course"][0]["course_id"],"c1")

    def test_invalid_usage_is_unknown_not_zero(self):
        record_usage(self.s,{"usage":{"total_tokens":-1,"prompt_tokens":True}},429,"model")
        t=usage_summary(self.s)["totals"]
        self.assertIsNone(t["total_tokens"])
        self.assertIsNone(t["prompt_tokens"])
        self.assertEqual(t["unknown_requests"],1)

    def test_invalid_json_still_counts_attributed_usage(self):
        self.s.set_meta("current_course_id","course-1")
        self.s.set_meta("current_action_id",42)
        client=GroqClient(self.s,key_loader=lambda:"test",transport=lambda *a:(200,{},
            {"usage":{"total_tokens":15},"choices":[{"finish_reason":"stop","message":{"content":"bad"}}]}))
        with self.assertRaises(GroqError): client._request({"model":"test-model"})
        with self.s._connection() as c:
            row=dict(c.execute("select * from groq_usage").fetchone())
        self.assertEqual((row["course_id"],row["action_id"],row["total_tokens"]),("course-1",42,15))

    def test_retry_records_both_responses(self):
        replies=iter([(429,{"retry-after":"0"},{}),(200,{}, {"usage":{"total_tokens":7},"choices":[{"finish_reason":"stop","message":{"content":"{}"}}]})])
        client=GroqClient(self.s,key_loader=lambda:"test",transport=lambda *a:next(replies),sleep=lambda _:None)
        client._request({"model":"model"})
        t=usage_summary(self.s)["totals"]
        self.assertEqual((t["requests"],t["unknown_requests"],t["total_tokens"]),(2,1,7))

    def test_cached_answer_adds_no_usage(self):
        q={"text":"Choose X","kind":"single","options":[{"id":"a","text":"X"}]}
        response={"usage":{"prompt_tokens":4,"completion_tokens":2,"total_tokens":6},
            "choices":[{"finish_reason":"stop","message":{"content":json.dumps({"cannot_answer":False,"selected":["a"]})}}]}
        client=GroqClient(self.s,key_loader=lambda:"test",transport=lambda *a:(200,{},response))
        client.answer(q)
        client.answer(q)
        self.assertEqual(usage_summary(self.s)["totals"]["requests"],1)
        self.assertEqual(usage_summary(self.s)["totals"]["total_tokens"],6)

    def test_status_api(self):
        import threading
        import urllib.request
        server=Dashboard(self.s,Control(self.s),port=0).build_server()
        thread=threading.Thread(target=server.serve_forever,daemon=True); thread.start()
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{server.server_port}/api/status") as response:
                data=json.load(response)
            self.assertEqual(data["groq_usage"]["totals"]["requests"],0)
            self.assertEqual(data["control_state"],"stopped")
        finally:
            server.shutdown(); server.server_close(); thread.join()

    def test_panel_and_escape(self):
        data=usage_summary(self.s)
        data["by_day_course"]=[dict(day="2026-10-22",course="<script>",requests=1,prompt_tokens=1,completion_tokens=2,total_tokens=3,unknown_requests=0)]
        self.assertIn("&lt;script&gt;",usage_html(data))
        html=Dashboard(self.s,Control(self.s))._render()
        self.assertIn("Расход токенов Groq",html)
        self.assertIn("0.2.40",html)

if __name__ == "__main__": unittest.main()

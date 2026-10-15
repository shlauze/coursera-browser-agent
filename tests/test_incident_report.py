"""Incident reports must help diagnosis without persisting private page data."""

import json
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest


sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from incident_report import write_incident


class IncidentReportTests(unittest.TestCase):
    def test_writes_stable_structured_record_without_exception_message(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = "sk-test-private-api-key-123"
            question = "Which answer is correct for the protected assessment?"
            path = write_incident(
                Path(directory), course_id="deep-learning-with-pytorch",
                operation="open_item", retry_code="dom_hydration",
                item_key="W3x2U", exc=RuntimeError(f"{secret}; {question}"),
            )

            self.assertEqual(path, Path(directory) / "incident.json")
            raw = path.read_text(encoding="utf-8")
            record = json.loads(raw)
            self.assertEqual(record["schema_version"], 1)
            self.assertEqual(record["course_id"], "sha256:" + hashlib.sha256(b"deep-learning-with-pytorch").hexdigest()[:16])
            self.assertEqual(record["operation"], "open_item")
            self.assertEqual(record["retry_code"], "dom_hydration")
            self.assertEqual(record["item_key"], "sha256:" + hashlib.sha256(b"W3x2U").hexdigest()[:16])
            self.assertEqual(record["error_class"], "RuntimeError")
            self.assertRegex(record["timestamp_utc"], r"\A\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z\Z")
            self.assertIn("checkpoint", record["recovery"])
            self.assertNotIn(secret, raw)
            self.assertNotIn(question, raw)
            self.assertNotIn("deep-learning-with-pytorch", raw)
            self.assertNotIn("W3x2U", raw)
            self.assertNotIn("RuntimeError:", raw)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])

    def test_invalid_identifiers_and_url_paths_are_replaced_not_partially_logged(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = "sk-test-another-private-key"
            url = f"https://coursera.org/learn/course/lecture/id?token={secret}"
            path = write_incident(
                Path(directory), course_id=url, operation=f"open?token={secret}",
                retry_code=f"{secret} | invalid", item_key="../../private/path",
                exc=ValueError("assessment text"),
            )
            raw = path.read_text(encoding="utf-8")
            record = json.loads(raw)
            self.assertEqual(
                {key: record[key] for key in ("course_id", "operation", "retry_code", "item_key")},
                {key: "unknown" for key in ("course_id", "operation", "retry_code", "item_key")},
            )
            self.assertNotIn(secret, raw)
            self.assertNotIn("coursera.org", raw)
            self.assertNotIn("assessment text", raw)
            self.assertNotIn("private/path", raw)

    def test_credential_shaped_and_unknown_opaque_identifiers_are_never_copied(self):
        for secret in ("sk-test-another-private-key", "gsk_fakeGroqKey123",
                       "ghp_fakeGithubToken123", "OpaqueUnrecognizedKey123"):
            with self.subTest(secret=secret), tempfile.TemporaryDirectory() as directory:
                path = write_incident(Path(directory), course_id=secret,
                                      operation=secret, retry_code=secret,
                                      item_key=secret, exc=RuntimeError("hidden"))
                raw = path.read_text(encoding="utf-8")
                record = json.loads(raw)
                self.assertEqual(record["operation"], "unknown")
                self.assertEqual(record["retry_code"], "unknown")
                self.assertIn(record["course_id"], ("unknown", "sha256:" + hashlib.sha256(secret.encode()).hexdigest()[:16]))
                self.assertEqual(record["course_id"], record["item_key"])
                self.assertNotIn(secret, raw)

    def test_replaces_previous_incident_and_bounds_identifier_length(self):
        with tempfile.TemporaryDirectory() as directory:
            first = write_incident(Path(directory), course_id="course-a", operation="scan",
                                   exc=RuntimeError("first secret"))
            second = write_incident(Path(directory), course_id="course-b",
                                    operation="x" * 97, item_key=object(),
                                    exc=LookupError("second secret"))
            self.assertEqual(first, second)
            raw = second.read_text(encoding="utf-8")
            record = json.loads(raw)
            self.assertEqual(record["course_id"], "sha256:" + hashlib.sha256(b"course-b").hexdigest()[:16])
            self.assertEqual(record["operation"], "unknown")
            self.assertEqual(record["item_key"], "unknown")
            self.assertEqual(record["error_class"], "LookupError")
            self.assertNotIn("course-a", raw)
            self.assertNotIn("secret", raw)
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()

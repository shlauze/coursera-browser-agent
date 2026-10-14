"""The live progress log must survive brief Windows file sharing conflicts."""

import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import course_state
import coursera_start


class ProgressAtomicTests(unittest.TestCase):
    def test_progress_save_retries_access_denied_without_losing_old_version(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "progress.json"
            coursera_start.save_json(path, {"revision": 1})
            original_replace = course_state.os.replace
            attempts = []

            def briefly_blocked(source, destination):
                attempts.append(Path(source))
                self.assertEqual(Path(destination), path)
                self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                                 {"revision": 1})
                self.assertEqual(json.loads(Path(source).read_text(encoding="utf-8")),
                                 {"revision": 2})
                if len(attempts) <= 2:
                    error = PermissionError("simulated Windows sharing violation")
                    error.winerror = 5
                    raise error
                return original_replace(source, destination)

            with patch.object(course_state.os, "replace", side_effect=briefly_blocked), \
                 patch.object(course_state.time, "sleep") as sleep:
                coursera_start.save_json(path, {"revision": 2})

            self.assertEqual(len(attempts), 3)
            self.assertEqual(len(set(attempts)), 1)
            self.assertEqual(sleep.call_count, 2)
            self.assertEqual(json.loads(path.read_text(encoding="utf-8")),
                             {"revision": 2})
            self.assertEqual(list(Path(directory).glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()

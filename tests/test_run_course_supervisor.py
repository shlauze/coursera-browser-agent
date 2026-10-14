"""Isolated smoke tests for the Windows batch crash supervisor."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
TEST_ROOT = (ROOT / "tests").resolve()


@unittest.skipUnless(sys.platform == "win32", "Windows batch file")
class RunCourseSupervisorTests(unittest.TestCase):
    def setUp(self):
        self.fixture = Path(tempfile.mkdtemp(prefix="supervisor-smoke-", dir=TEST_ROOT)).resolve()
        self.assertTrue(self.fixture.is_relative_to(TEST_ROOT))
        scripts = self.fixture / ".venv" / "Scripts"
        scripts.mkdir(parents=True)
        shutil.copy2(ROOT / ".venv" / "Scripts" / "python.exe", scripts / "python.exe")
        shutil.copy2(ROOT / ".venv" / "pyvenv.cfg", self.fixture / ".venv" / "pyvenv.cfg")
        batch = (ROOT / "run_course.cmd").read_text(encoding="utf-8")
        self.assertIn("set /a restartDelay=restartCount*10", batch)
        self.assertIn("if not defined unattended pause", batch)
        batch = batch.replace("set /a restartDelay=restartCount*10", "set /a restartDelay=0")
        batch = batch.replace("if not defined unattended pause", "if not defined unattended echo TEST_PAUSE")
        (self.fixture / "run_course.cmd").write_text(batch, encoding="utf-8")
        (self.fixture / "course_queue.py").write_text(
            """import json, os, pathlib, sys
root = pathlib.Path(__file__).resolve().parent
counter = root / 'counter.txt'
count = int(counter.read_text()) + 1 if counter.exists() else 1
counter.write_text(str(count))
(root / ('args-' + str(count) + '.json')).write_text(json.dumps(sys.argv[1:]))
if count > 1 and '--yes' not in sys.argv:
    raise SystemExit(42)
scenario = os.environ['FAKE_SCENARIO']
if scenario == 'attention':
    status, code = 'attention', 2
elif scenario == 'crash_once' and count > 1:
    status, code = 'completed', 0
else:
    status, code = 'running', 1
(root / 'state').mkdir(exist_ok=True)
(root / 'state' / 'queue.json').write_text(json.dumps({'status': status}))
raise SystemExit(code)
""", encoding="utf-8")

    def tearDown(self):
        self.assertTrue(self.fixture.is_relative_to(TEST_ROOT))
        shutil.rmtree(self.fixture)

    def run_fixture(self, scenario, *arguments):
        env = {**os.environ, "FAKE_SCENARIO": scenario}
        return subprocess.run(
            ["cmd.exe", "/d", "/c", str(self.fixture / "run_course.cmd"), *arguments],
            cwd=self.fixture, env=env, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
            encoding="utf-8", errors="replace", timeout=25)

    def test_crash_restarts_with_yes_and_saved_state(self):
        result = self.run_fixture("crash_once", "--yes")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertEqual((self.fixture / "counter.txt").read_text(), "2")
        self.assertIn("--yes", json.loads((self.fixture / "args-1.json").read_text()))
        self.assertIn("--yes", json.loads((self.fixture / "args-2.json").read_text()))
        self.assertNotIn("TEST_PAUSE", result.stdout)
        self.assertIn("Unexpected exit 1", (self.fixture / "logs" / "runner_supervisor.log").read_text())

    def test_interactive_first_run_restarts_without_prompt(self):
        result = self.run_fixture("crash_once")
        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertNotIn("--yes", json.loads((self.fixture / "args-1.json").read_text()))
        self.assertIn("--yes", json.loads((self.fixture / "args-2.json").read_text()))
        self.assertIn("TEST_PAUSE", result.stdout)

    def test_intentional_attention_does_not_restart(self):
        result = self.run_fixture("attention")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual((self.fixture / "counter.txt").read_text(), "1")
        self.assertIn("TEST_PAUSE", result.stdout)

    def test_unattended_attention_does_not_pause(self):
        result = self.run_fixture("attention", "--minutes", "180", "--yes")
        self.assertEqual(result.returncode, 2, result.stdout)
        self.assertEqual((self.fixture / "counter.txt").read_text(), "1")
        self.assertNotIn("TEST_PAUSE", result.stdout)

    def test_persistent_crash_stops_after_three_restarts(self):
        result = self.run_fixture("always_crash")
        self.assertEqual(result.returncode, 1, result.stdout)
        self.assertEqual((self.fixture / "counter.txt").read_text(), "4")


if __name__ == "__main__":
    unittest.main()

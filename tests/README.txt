Offline regression suite

Run from the browser-agent directory:
  .\run_tests.cmd
Or:
  .venv\Scripts\python.exe -m unittest discover -s tests -p "test_*.py" -v

Requires the project Python dependencies and Node.js in PATH.
The suite uses fake browser/model clients and saved minimal fixtures.
It does not open Chrome, contact Coursera or request Groq answers.
Monitor tests start temporary HTTP servers on local loopback only.
All state/checkpoint writes use temporary directories.
No work/ folder or original logs/ folder is required to run these tests.
Synthetic fixtures preserve form shapes, UI labels, control counts, and state transitions needed to reproduce the bugs.
They contain generated example choices and no learner responses; stable legacy fixture paths are test identifiers.
package_manifest.json describes current test sources and fixtures using repository LF bytes.

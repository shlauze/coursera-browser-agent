# Offline regression coverage and fixture privacy

The repository contains regression tests for browser-agent behavior using mocked browser/model clients, local DOM harnesses, temporary state, and synthetic inputs. Test fixtures describe UI states; they do not represent a learner account or a successful course completion.

The legacy suite covers delayed form controls, stable answer fingerprints, entry-button races, nested submit confirmation, processing and feedback states, new-attempt dialogs, outline loading, queue recovery, and safe handling of ambiguous results. The v2 suite additionally covers local browser DOM fixtures, notebook integration, persistent action recovery, overlays, and certificate checks.

Files under `tests/fixtures/logs/` are synthetic regression records. Their legacy directory names remain stable because tests reference them. Question/option text, answer identifiers and selected options are synthetic; unrelated page controls and learner responses have been removed. Required UI labels and fixed public item keys remain solely to exercise URL guards and state transitions. Fingerprints are calculated from the synthetic form data. Example outcome values are test inputs, not a learner history.

`courses.txt` starts with a disabled example and contains no configured learner queue. Runtime credentials, browser profiles, databases, answer stores, logs, backups, environments, and builds are excluded from the current publication. These changes do not rewrite or erase earlier Git history.

Run the legacy offline suite from the repository root with `run_tests.cmd`. Run the v2 suite from `browser-agent-v2` with its own `run_tests.cmd` after installing `requirements-dev.txt`; its browser and notebook fixtures run locally. The two suites also run as independent Windows jobs in `.github/workflows/tests.yml`. Neither suite requires a signed-in Coursera profile or live model requests.

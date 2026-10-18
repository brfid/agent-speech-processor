# Working on agent-speech-processor

Read [STATUS.md](STATUS.md) and the guide for your task. The complete
[agent protocol](docs/agents.md) owns intake, economical delegation, required role
review and handoff. Archive instructions, session guides and statuses own scope,
authorization, descriptive evidence, progress and next action.

Preserve original media, raw output, baselines, receipts and user edits. Edit only
authorized canonical text. Follow [evidence](docs/evidence.md) for speech and role
preservation; machine completion is separate from audio checking. Read and fetch
only the selected files. Keep private data, weights and configuration outside Git.

Use only current interfaces and formats. [Workflow](docs/workflow.md) owns module
boundaries and recovery. Supported commands stay independent of optional review
code. Reading and playback require no model or verification gate.

Keep each procedure in its owning guide and link to it. Do not add author bylines
or personal examples.

## Checks

Use Python 3.11 or newer and the
[audio dependencies](docs/quickstart.md#1-install-the-command), then run:

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e '.[test]'
.venv/bin/python -m unittest discover -s tests -v
git diff --check
```

Use synthetic data. Check preservation, interruption and output contracts when
changing behavior. Keep private archive files out of tests and release artifacts.

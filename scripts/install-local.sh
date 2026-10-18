#!/bin/sh
# Install this checkout into its own virtual environment. Never fetch models.
set -eu
speech_processor_root=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [ -x "$speech_processor_root/.venv/bin/python" ]; then
    speech_processor_python="$speech_processor_root/.venv/bin/python"
else
    speech_processor_python=${AGENT_SPEECH_PROCESSOR_PYTHON:-python3}
fi
"$speech_processor_python" -c 'import sys; sys.exit("agent-speech-processor requires Python 3.11 or newer") if sys.version_info < (3, 11) else None'
if [ ! -x "$speech_processor_root/.venv/bin/python" ]; then
    "$speech_processor_python" -m venv "$speech_processor_root/.venv"
fi
"$speech_processor_root/.venv/bin/python" -m pip install -e "$speech_processor_root"
"$speech_processor_root/.venv/bin/agent-speech-processor" --version
printf '\nInstalled this checkout. Run: %s/.venv/bin/agent-speech-processor --help\n' "$speech_processor_root"

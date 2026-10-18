# agent-speech-processor

Create local Whisper drafts, read and edit timecoded transcripts, and play short
passages from the original recording. The toolkit does not call an LLM.

## Install

Use Python 3.11 or later. Audio work needs FFmpeg/FFprobe; drafting also needs a
separately installed whisper.cpp engine and model.

```sh
git clone https://github.com/brfid/agent-speech-processor.git
cd agent-speech-processor
sh scripts/install-local.sh
. .venv/bin/activate
agent-speech-processor --help
```

Follow the [quickstart](docs/quickstart.md) for dependencies and first use.

## Use

Keep recordings, transcripts, weights and local configuration outside Git. Raw
recognition and the machine baseline are preserved separately from working text.
Reading and playback need no recognition model. Draft wording and speaker roles
remain uncertain until checked against the source.

| Guide | Contents |
| --- | --- |
| [Agents](docs/agents.md) | Intake, delegation, role review and completion |
| [Workflow](docs/workflow.md) | Processing, current formats and recovery |
| [Reading](docs/reading.md) | Canonical transcripts, promotion and search |
| [Evidence](docs/evidence.md) | Speech preservation and review claims |
| [Models](docs/models.md) | Dependencies and unqualified review research |
| [Releasing](docs/releasing.md) | Release checks |

See [STATUS.md](STATUS.md) for capabilities and checks. [MIT licensed](LICENSE).

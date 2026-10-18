# Quickstart

This guide installs a local checkout and processes one explicit recording.
Keep your working audio, outputs, models, and configuration outside the checkout.
The repository contains software, fictional examples, and synthetic tests.

## 1. Install the command

You need Git, Python 3.11 or newer, FFmpeg/FFprobe, and a local player for
`--play`: `afplay` on macOS or `ffplay`. Building whisper.cpp also needs CMake and
a C++ compiler. On macOS with Homebrew and the Xcode command-line tools installed:

```sh
brew install python@3.11 ffmpeg cmake
```

On a recent Debian/Ubuntu system whose Python is at least 3.11:

```sh
sudo apt-get update
sudo apt-get install python3 python3-venv python3-pip ffmpeg cmake build-essential git
```

Then clone and install:

```sh
git clone https://github.com/brfid/agent-speech-processor.git
cd agent-speech-processor
sh scripts/install-local.sh
. .venv/bin/activate
agent-speech-processor --version
agent-speech-processor --help
```

Set `AGENT_SPEECH_PROCESSOR_PYTHON=python3.11` before the installer if your default Python is older.
The installer creates `.venv` and installs this checkout in editable mode. It
reuses an existing environment's Python when one is already present. It
installs no recognition models or experimental review packages. Dependencies may
need internet access during setup; transcription and playback run locally.

## 2. Try playback without a model

Make a disposable directory outside the checkout and generate three seconds of
tone using FFmpeg. This checks extraction without an interview or audio device:

```sh
AGENT_SPEECH_PROCESSOR_DEMO=$(mktemp -d)
ffmpeg -v error -n -f lavfi -i 'sine=frequency=440:duration=3' \
  -ar 16000 -ac 1 -c:a pcm_s16le "$AGENT_SPEECH_PROCESSOR_DEMO/tone.wav"
agent-speech-processor passage --source "$AGENT_SPEECH_PROCESSOR_DEMO/tone.wav" --start 1 --end 2 \
  --output "$AGENT_SPEECH_PROCESSOR_DEMO/passage.wav"
```

The command returns JSON describing the requested and actual source interval,
selected streams, and saved path. It never replaces an existing output. Use
`--play` instead of `--output` to listen with an installed local player. A player
exiting successfully does not establish that someone heard or verified anything.

## 3. Install Whisper separately

Follow the [upstream whisper.cpp build guide](https://github.com/ggml-org/whisper.cpp#quick-start).
For a source build outside the agent-speech-processor checkout:

```sh
mkdir -p "$HOME/.local/share/agent-speech-processor/tools"
git clone https://github.com/ggml-org/whisper.cpp.git \
  "$HOME/.local/share/agent-speech-processor/tools/whisper.cpp"
cmake -S "$HOME/.local/share/agent-speech-processor/tools/whisper.cpp" \
  -B "$HOME/.local/share/agent-speech-processor/tools/whisper.cpp/build"
cmake --build "$HOME/.local/share/agent-speech-processor/tools/whisper.cpp/build" -j --config Release
export PATH="$HOME/.local/share/agent-speech-processor/tools/whisper.cpp/build/bin:$PATH"
whisper-cli --help
```

Choose a release or commit before building if you need a fixed installation.
Each draft records the installed binary, model, and processing identities.
This does not promise identical recognition from a different environment.

Download one model explicitly with the upstream script. This example uses
`base.en` for a small English smoke test; it is not a claim of sufficient quality
for your interviews:

```sh
(
  cd "$HOME/.local/share/agent-speech-processor/tools/whisper.cpp"
  sh models/download-ggml-model.sh base.en
)
AGENT_SPEECH_PROCESSOR_MODEL="$HOME/.local/share/agent-speech-processor/tools/whisper.cpp/models/ggml-base.en.bin"
```

You can supply an existing compatible ggml model instead. Model installation is
separate from agent-speech-processor installation. Compare candidate models on your own material;
keep changed model/settings runs in different output folders. Read the external
model and tool licenses before redistributing them.

## 4. Draft one recording

For archive material, [bound the intake](agents.md#bound-the-intake) before drafting.
Locate related files and reconcile existing work within the authorized scope.

For a wholly fictional input, generate speech from the included text. On macOS
with an installed system voice:

```sh
say -o "$AGENT_SPEECH_PROCESSOR_DEMO/demo.aiff" -f examples/demo.txt
AGENT_SPEECH_PROCESSOR_SOURCE="$AGENT_SPEECH_PROCESSOR_DEMO/demo.aiff"
```

On Linux with `espeak-ng` already installed:

```sh
espeak-ng -f examples/demo.txt -w "$AGENT_SPEECH_PROCESSOR_DEMO/demo.wav"
AGENT_SPEECH_PROCESSOR_SOURCE="$AGENT_SPEECH_PROCESSOR_DEMO/demo.wav"
```

Alternatively set `AGENT_SPEECH_PROCESSOR_SOURCE` to an explicit recording you are authorized to
process. Keep the original unchanged. Choose a new output folder:

```sh
AGENT_SPEECH_PROCESSOR_OUTPUT="$AGENT_SPEECH_PROCESSOR_DEMO/draft-v1"
agent-speech-processor draft --source "$AGENT_SPEECH_PROCESSOR_SOURCE" --output "$AGENT_SPEECH_PROCESSOR_OUTPUT" \
  --model "$AGENT_SPEECH_PROCESSOR_MODEL" --limit-chunks 1
agent-speech-processor verify --output "$AGENT_SPEECH_PROCESSOR_OUTPUT"
```

The short synthetic example may finish in one chunk. A longer recording stops
after the first new chunk. Inspect `transcript-draft.md`, `transcript-edited.md`,
and the reported `draft_completion`; byte integrity alone does not mean the
whole recording was drafted. The default is CPU, 120-second cores, five seconds
of audio context on each side, and zero recognizer text context. `--gpu` explicitly
enables your whisper.cpp build's GPU backend. At least 2 GiB of free working
space must remain in addition to the bounded processing allocation.

For multiple audio tracks, first inspect them:

```sh
ffprobe -v error -show_streams -show_format -of json "$AGENT_SPEECH_PROCESSOR_SOURCE"
```

`--audio-stream-index` counts audio streams from zero. The default is the first
audio stream. Repeat the option to select and mix other audio streams while
preserving source delays. A track number does not identify a speaker.

Finish or resume using the same processing flags, removing only the limit:

```sh
agent-speech-processor draft --source "$AGENT_SPEECH_PROCESSOR_SOURCE" --output "$AGENT_SPEECH_PROCESSOR_OUTPUT" --model "$AGENT_SPEECH_PROCESSOR_MODEL"
agent-speech-processor verify --output "$AGENT_SPEECH_PROCESSOR_OUTPUT"
```

Completed chunks and prepared responses are reused. Do not erase receipts or
staging to force a restart. A different source, model, processing code/runtime,
or decoding setting requires a new variant; see [recovery](workflow.md#resume-and-failures).

## 5. Complete the working transcript and check the source

Read the raw working Markdown, including HTML comments. Preserve the machine
baseline and raw JSON. Before promotion, the working file is
`transcript-edited.md`; preserve its generated preface and pending-role note.
For completed archive sessions, follow
[promotion and reading](reading.md); the reading folder's registered
`Transcript*.md` becomes the current working file and the old edited file stays
a historical snapshot. Put corrections and interpretation notes in the current
file, preserving timecodes and unresolved wording. Reruns preserve it.

Where archive policy requires text-inferred roles, normal processing also includes
a contextual review of every timed block and `roles --check` before handoff.
The generated `Unclear speakers` seed reports review pending. Follow the complete
[agent completion protocol](agents.md#completing-a-processing-request) and
[evidence contract](evidence.md#speech-and-editorial-preservation); text-inferred
roles do not verify voices, exact turns or audio.

For archive sessions, complete the [handoff checks](agents.md#hand-off-the-session)
and update the collection status and browse index before reporting completion.

Use [the agent guide](agents.md) with any permitted local-file-reading agent.
No provider integration is needed. Follow [evidence](evidence.md) when making
claims or attributing speech. Source passages use original recording seconds or
`HH:MM:SS.mmm`; the entire passage including context must fit within 30 seconds.

## Optional local configuration and iCloud

Explicit drafting/playback commands need no workspace file. If you want a
remembered session or player, create `~/.config/agent-speech-processor/workspace.json` outside Git.
Start with [the generic example](../examples/workspace.example.json), replacing
its placeholder values with your actual paths. Omit settings you do not use:

- `active_session`: default manifest for `inspect`, `reading` and experimental `review`.
- `archive`, `whisper_model`, `research_artifacts`: locations displayed by `workspace`.
  Optional source-workspace locations can also be stored as string path settings.
  Drafting still takes `--source`, `--output` and `--model` explicitly when
  recognition remains.
- `player`: an installed `afplay` or `ffplay` executable, without shell arguments.
  If absent, playback searches for `afplay`, then `ffplay` on PATH.

```sh
agent-speech-processor workspace
agent-speech-processor --config '/absolute/workspace.json' workspace
```

`--config` goes before the subcommand. Schema `1` takes string path/settings
values. An absent configuration is fine; invalid existing JSON is an error.
The sole default path is `~/.config/agent-speech-processor/workspace.json`.

For iCloud files, install `icmat` separately and add `--fetch` to the relevant
command. Discovery uses `AGENT_SPEECH_PROCESSOR_ICMAT` when set, then `icmat` on
PATH, then the generic `~/bin/icmat` fallback. Set the environment variable to an
executable path, without shell arguments, for another installation.
agent-speech-processor fetches only exact selected files; it does not scan the archive.
Ordinary local files need neither iCloud nor `icmat`. Fetch proves point-in-time
readability, not cloud freshness. Your collection and session records own your
interview progress; this repository's status describes software capability.

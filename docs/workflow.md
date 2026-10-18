# Workflow and recovery

## Ownership

`cli.py` parses arguments and dispatches operations. Supported processing has
these boundaries:

| Module | Responsibility |
| --- | --- |
| `inputs.py` | Exact source selection and optional iCloud materialization. |
| `timestamps.py` | Recording-position parsing and formatting. |
| `audio.py` | PCM constants, source timing, extraction filters, probing and PCM identity. |
| `clips.py` / `passage.py` | Bounded clip preparation and synchronous local playback. |
| `records.py` / `processes.py` | Atomic files, canonical digests and bounded subprocesses. |
| `job.py` | Generic job locks, immutable settings, checkpoints and checked outputs. |
| `drafts.py` / `session.py` | Chunk planning, read-only draft intake, completion and session reports. |
| `verification.py` / `retention.py` | Integrity reports and validated disposal of reproducible WAVs. |
| `whisper.py` / `provenance.py` | Sequential recognition and pinned processing identities. |
| `transcripts.py` | Protected baseline rendering and once-created editorial seed. |
| `working.py` | Canonical text registration and manifest agreement, without editable-text hash gates. |
| `promotion.py` | Preserved reading copies, audited link relocation and resumable registration. |
| `reading.py` | Explicit preferred-text/note selection and preserved README reading guides; no audio or receipt gate. |
| `reading_policy.py` | The explicit recording-disposition rule shared by reading and promotion. |
| `role_text.py` | Pure role-block parsing, readiness and exact label-only rendering, shared by draft, promotion, reading and role review. |
| `roles.py` | Full-text review packets, frozen context/selection, preserved before/after evidence and resumable role publication; no model or audio. |
| `markup.py` | Opaque Markdown/comment regions; each consumer owns its operation-specific rules. |
| `publication.py` | Regular-file checks, folder writer locks and immutable evidence-file creation for text workflows. |

Drafting and short clips share recording-relative extraction filters while keeping
their different window limits and direct-mix/separate-track policies. Job mechanics
receive validated missing-output exceptions from the audio adapter; they do not
import retention policy themselves. Reading and playback import no reviewer code.

Unqualified review lives under `agent_speech_processor.experimental`: `pipeline.py` orchestrates
windows, `review.py` validates proposals, `audio.py` adds voice-activity alarms,
`metrics.py` accounts for coverage/error, `render.py` protects local reviewed
outputs, and `evaluate.py` applies reference gates. Only `experimental/worker.py`
loads optional review weights. Whisper runs in its own child executable.

There is no server, database or queue. All processing is sequential. Model calls
have timeouts and process-group cleanup. A model exits between calls to release
unified memory; this is deliberately simple, though it adds loading overhead.

## Session manifest

Keep a `session.json` beside the session README in the archive. Paths resolve
relative to that manifest; recording order is explicit. The recipe digest is
SHA-256 of the draft receipt's `specification.settings`, serialized as sorted
compact JSON (`records.digest`). It pins the draft the operator selected.

```json
{
  "schema": 1,
  "vocabulary": ["a documented technical term"],
  "recordings": [{
    "source": "../../originals/recording.mov",
    "source_sha256": "<64 lowercase hexadecimal characters>",
    "draft": "Recording one",
    "draft_recipe_sha256": "<64 lowercase hexadecimal characters>",
    "audio_streams": [0, 1]
  }]
}
```

The importer checks original identity, the pinned recipe, all completed output
hashes, exact chunk coverage and usable timestamps. Existing draft receipts and
artifacts are never rewritten by import or review. Draft generation defaults
to `--chunk-seconds 120 --max-context 0`. Both can be overridden;
`--max-context default` deliberately uses the recognizer default. These settings
limit recognizer text carryover; they do not prove recognition accuracy.

Promotion creates a separate reading-folder `.session.json` with schema `1` and
`reading_layout: 1`. It preserves the recording fields above and adds a relative
`working_transcript` path per recording. Each selected job gets a small
`.working-transcript.json` pointing to the new manifest. Source manifests and
receipts are not rewritten. The mapping selects canonical text; the current
README owns descriptive evidence. See [promotion and reading](reading.md).

Selected tracks retain their original delays and early endings, padded to the
requested source window before mixing/recognition. Duration follows the recording
timeline so a delayed track's tail is retained.

## Machine and edited transcripts

Each newly completed recording has a protected `transcript-draft.md` and a separate
`transcript-edited.md`. Both identify the recording, original link, time basis,
selected streams and absence of audio-checked coverage. The protected baseline
has no role labels; the editable seed uses pending `Unclear speakers` roles.
Source links and receipt source paths are relative to the recording folder, so
moving an archive as a unit preserves them.

New editable seeds use `Unclear speakers` and an explicit role-review-pending
note. Protected machine baselines retain raw recognition. Archive-required
contextual review uses `agent-speech-processor roles` after promotion, as part of normal processing.
The agent reviews the exported full-text packet and fills every block's role;
the tool validates and applies only labels plus the qualifying note, saving exact
before/after evidence under `.role-review/` in the reading folder. It preserves
raw baselines and historical snapshots. Follow the
[agent completion protocol](agents.md#completing-a-processing-request) and record
its coverage separately. `agent-speech-processor roles --check` exits 2 while the review is pending;
normal handoff requires it to pass. Draft, promotion and reading results report
role readiness separately from mechanical completion. Role review accepts
`Interviewer`, `Interviewee` and `Unclear speakers` labels with the current notes.

Role packets pin the current guides, manifest/registration controls, promotion
state and recording-disposition inputs, including absent guides or policy files.
Changes to this context require a fresh packet. Application rechecks the full
selection under folder locks, stages all evidence before publishing any transcript,
then checks the pins before each write. Exact UTF-8 bytes, including line endings,
are preserved outside the labels and explanatory note. Code and HTML annotations
remain opaque text. Relocation-specific Markdown restrictions belong only to
promotion. Role readiness accepts normally wrapped qualifying notes; it does not impose
promotion's link-relocation syntax on ordinary annotations or examples.

For an authorized completed session, `agent-speech-processor promote --session PATH --output DIR`
creates a plain reading folder. Optional `--notes PATH` supplies its README;
`--fetch` materializes exact controls/text and never audio. Canonical
`Transcript*.md` files then own further edits. The old job-local edited files
remain historical snapshots. Reading requires the canonical manifest and job
registration to agree. Draft reruns preserve registered text. Missing registered
text stops for restoration.

The machine baseline retains recognizer wording and repeated chunk context.
Raw JSON preserves original recognizer offsets. Rendered times are bounded to the
actual source extent, including millisecond rounding at the end. Small overruns
(up to 50 ms) are labeled rounding adjustments; larger overruns are explicitly
flagged. Reversed/empty or wholly out-of-source segments stop rendering while the
completed raw response remains available for investigation.

The initial editorial pass groups short passages. Matching ignores case and
whitespace only: signs, punctuation, decimal forms, units and other symbols remain
significant (`-5 million` and `5 million` are different readings). Removing a
suffix/prefix requires at least four words, a preceding segment boundary wholly
inside shared context, and the same segment start in both recognitions. Fully
consumed segments must also share an end. Merely intersecting broad segment spans
does not establish duplication; ambiguous repetitions remain.

After any prefix removal, the remaining text is independently checked against
the preceding recognition. Unresolved overlap has a visible alternative/repetition
warning, an HTML explanation and source spans. Partially trimmed segments keep
their original spans because word alignment is unknown. No fuzzy match, preferred
reading or midpoint filter discards wording. Paragraphs do not identify voices.
This is a conservative mechanical edit, not a full textual correction or listening
pass; further editing and interpretation use ordinary file tools. This rule applies
only when creating a new edited file; existing edited files and notes are untouched.

The edited file is created exclusively once, outside the machine-output hash
checks. A rerun preserves existing edits/comments. An absent file that was already
created is an error requiring restoration, not permission to regenerate over lost
notes. The receipt records the baseline and initial editorial hash/method; these
are historical creation evidence, not a requirement that the working file stay
unchanged. Promotion preserves that seed and its state, shortens only the current
generated preface with its pending note, and audits rebased Markdown link targets.
Timecoded body text and annotations survive. Changed or ambiguous input stops
for inspection. No provider, SDK, interpretation of annotation contents or
annotation database is involved.

## Storage and replay

Jobs retain raw JSON, small SRT/TXT exports, recognition `diagnostics/`, both Markdown files, `media.json`,
`recipe.json`, `recipe-source.zip` and processing/iCloud receipts. A WAV exists
only during its chunk's model call unless `--keep-audio` is selected. The durable
chunk record includes exact extraction arguments, PCM and WAV hashes, frame count,
format and source offsets. Completed responses can resume without a WAV or another
model call. Drafting checks temporary/output space before work and each new chunk,
allowing for staging and a 2 GiB reserve.

The replay recipe retains model identity (plus revision when a matching
`--model-receipt` is supplied), tool binary hashes and version/help text, platform,
hardware, thread environment, and exact recognition arguments. On macOS it also
hashes recursively resolved non-system linked libraries and the actual dynamically
loaded recognition backends; unresolved dependencies
are explicitly listed. OS libraries are bounded by the recorded OS release.
The source ZIP is a small deterministic snapshot of the exact Python package.
Volatile GPU startup timings are excluded from the recorded help/recipe identity.
Content identities are separate from configured model/archive locations.
Other platforms explicitly report the limit of their library inventory.

The recipe's `compatibility` record is its recognition identity for resume. It
pins model bytes, all three tool identities and their libraries,
runtime/environment, command templates, processing settings
and explicit whole recognition modules listed in
[`provenance.DRAFT_MODULES`](../src/agent_speech_processor/provenance.py). An incomplete snapshot is
rejected. Playback, experimental review and editorial-rendering code remain in
the full recursive package snapshot but are outside the recognition identity.
The initial recipe and source ZIP are preserved on
matching resume, even if unrelated code changed. Changes to any pinned module,
including comments, change that identity; no dependency inference is performed.

Code moves and package-identity changes alter recognition identity. An unfinished
job whose pinned code differs needs its saved processing source or a new output
variant. Completed/prepared responses can still be reused without a model call;
their original recipes, source ZIPs and user-owned text are preserved.

Retained JSON and its saved rendering source can reconstruct the baseline.
Source plus extraction arguments can recreate PCM for comparison. Fresh recognition or editorial runs
can differ; preserve their outputs in new variants. To replay saved code,
extract the saved package outside the archive and use its directory on
`PYTHONPATH` with the recorded Python/tool/model versions. Run the CLI/module
actually present in that snapshot with the recorded settings and a fresh output
folder. The source snapshot includes its CLI; no server is required.

To dispose of retained WAVs in an explicitly selected current-format job:

```sh
agent-speech-processor prune-audio --output '/absolute/draft-job' --fetch
agent-speech-processor prune-audio --output '/absolute/draft-job' --fetch --apply
```

The first command previews exact recorded WAVs and logical bytes. The second
locks the job, verifies original and required outputs, recreates every candidate
WAV and compares its complete hash. Any mismatch stops before deletion. It
preserves `.processing.json` unchanged, saves a byte-identical historical copy,
then records `audio-retention.json` before removing only the verified chunk WAVs.
An interrupted cleanup leaves explicit retry state. Verification/import accept
only the listed absent WAVs; missing raw JSON or changed retained files still fail.
No original, transcript, user note, failed recognition result or unlisted file is
removed. Logical bytes removed do not guarantee immediate free-space recovery.

## Current formats and resume

The sole command is `agent-speech-processor`; imports use
`agent_speech_processor`. These are the current persisted formats:

| Record | Format |
| --- | --- |
| Whisper `.processing.json` | Schema `2`, relative source paths, `adapter_version: 4`, `recipe_version: 2`. |
| `recipe.json` | Schema `2`; its `compatibility` recognition-identity record has schema `1`. |
| Source `session.json` | Schema `1`; recording order, source/recipe hashes and audio streams. |
| Reading `.session.json` | Schema `1`, `reading_layout: 1`, relative `working_transcript` per recording. |
| `.working-transcript.json` | Schema `1`, relative `manifest` locator agreeing with the reading manifest. |
| Workspace, promotion, guide, role-review and retention controls | Schema `1`. |
| Experimental processing receipts and review records | Schema `1`. |

Schema numbers belong to each record type. Unsupported receipt, preface, role-note
or guide formats stop for inspection; they are not upgraded by normal commands.
Preserve receipts, raw results, source snapshots and working edits. Never rewrite
an identity to bypass resume checks. Use the saved processing source or an
authorized new variant when an unfinished job's recognition identity differs.
Completed/prepared response reuse follows [recovery](#resume-and-failures).

Promotion accepts the current generated edited preface followed by the current
pending-role note. Reading requires a registered canonical transcript. Guide
markers are:

```html
<!-- agent-speech-processor reading guide v1 -->
<!-- end agent-speech-processor reading guide -->
```

## Experimental review

```sh
agent-speech-processor experimental review --session '/absolute/session.json' \
  --variant 'candidate-01' --pilot --limit-windows 1 --fetch \
  --diarizer-model '/absolute/pinned-moss-folder' \
  --reviewer qwen2-audio --reviewer-model '/absolute/pinned-reviewer-folder'
```

`--limit-windows` limits new work **per recording**. The default 20-second cores
have two seconds of context on either side. Every clip must be at most 30 seconds.
FFmpeg extracts each selected stream on the original recording timeline, including
late-stream silence; an equal-weight mix accompanies the separate tracks. WebRTC
voice activity only flags possible missing speech. It never skips review calls.

Each recording gets `reviews/<variant>/` containing its own `.processing.json`,
`intake.json`, `windows/` requests/responses/diagnostics, and immutable reports in
`reports/`. Audio clips are temporary; retained hashes, source offsets, stream
identifiers and settings support recreation. `README.md` is created only if absent.
Detailed corrections and uncertainties are in reports, not hidden receipts.

A pilot never creates `transcript-reviewed.md`, even if all calls succeed.
Without `--pilot`, `--qualification PATH` must pass the exact recipe gate. Only
complete accepted coverage can publish `transcript-reviewed.md` in the existing
recording derivative folder, using an independent `.review-publication.json`.
Existing drafts, metadata and prior reviewed transcripts are never overwritten.

## Resume and failures

Repeat the selected draft command to verify and reuse completed work. Limits can
change; source identity and processing settings must still match. Remaining
recognition also requires the matching recipe identity described
above. Experimental review keeps its existing exact recipe and qualification gate.

Draft recognizers write JSON/SRT/TXT directly into the existing durable `.stage-*`
directory, while WAVs stay in the OS temporary directory by default. `input.json`
records the PCM identity and extraction arguments. `recognition.json` captures the
command, elapsed time, return code/error and the last 16 KiB each of stdout/stderr,
with truncation flags, before parsing or timestamp validation. Successful diagnostics
are published under `diagnostics/`; elapsed times are outside recipe identity.

The response is checkpointed before validation and publication. Malformed JSON,
invalid timestamps and command failure leave rejected/failed attempts and their
raw evidence in staging, with no accepted chunk coverage. Repeating a draft command
makes a new attempt and preserves earlier stage references in `failed_attempts`.
Prepared responses interrupted during validation or publication are checked and
reused without another recognition call. Modified prepared or published files stop
the job. Only a successfully published attempt's stage is removed; earlier failed
stages remain. Do not delete receipts to force reuse.

With every chunk completed or prepared and not rejected,
`draft` can finish publication/rendering or verify existing outputs without model
weights, whisper-cli, FFmpeg/FFprobe, runtime inventory or chunk-sized disk preflight.
Keep the original source/output and selected flags, but `--model` may be omitted.
This reuses the saved recipe; it does not qualify the currently installed runtime.
Source and output integrity checks, file publication space and the job lock still
apply. Missing user-owned edited text is an error on resume, never permission to
recreate lost notes. No command for rebuilding arbitrary fresh text variants is
added. If interruption occurs before the prepared checkpoint, raw evidence already
written to staging remains, but another recognition attempt may be needed. A hard
kill can prevent final diagnostics and temporary-WAV cleanup.

For experimental review, use `--retry-failed` to append an attempt while preserving
the rejected one. A changed
backend/prompt belongs in a new variant, not a retry. Source checks run before and
after a successful job and immediately before publication.

Exit codes: `0` success; `1` invalid input, failed command or integrity failure;
`2` a review/qualification is incomplete, experimental or failed its quality gate.
A bounded draft returns `0`; check its reported completion separately.

`verify` reports `byte_integrity` and any `integrity_errors` separately from
`draft_completion`: expected/completed chunks, combined-baseline presence,
job-local edited-file presence, machine-draft completion, current working-text
availability and overall completion. Expected chunks are unknown (`null`) until
the media probe is complete. A completed draft requires its edited seed or
registered canonical transcript. `working_transcript_registered`,
`working_transcript`, `working_transcript_present` and `working_transcript_error`
describe that selection. Mapping controls are checked; text availability uses
file metadata only, without reading/hashing the editable contents or fetching
audio. A missing registered file or mapping error makes overall completion false
while `machine_draft_complete` remains an independent result. Completion describes
recorded coverage and file presence, not recognition accuracy; a modified
recorded file separately fails byte integrity.

The `verified` field reports recorded-step integrity: exits are `1` for byte/integrity or
input errors, `2` for incomplete recorded steps, otherwise `0`. A bounded one-of-two
draft with intact existing files can therefore return `verified: true`,
`byte_integrity: true`, exit `0`, and `draft_completion.complete: false`. A missing
recorded baseline fails byte integrity; missing current working text affects completion.
Neither result gates ordinary reading or playback. These checks do not establish
speech accuracy, iCloud freshness, historical completeness or human listening.

## Reading and playback

`passage` needs
only the original source and time range, with optional context and audio-stream
selection. It never opens transcripts, draft receipts, qualifications or models.

```sh
agent-speech-processor passage --source '/absolute/recording.mov' \
  --start 00:12:14.000 --end 00:12:34.000 --context-seconds 2 \
  --audio-stream-index 0 --audio-stream-index 1 --play
agent-speech-processor passage --source '/absolute/recording.mov' \
  --start 734 --end 754 --output '/absolute/new-passage.wav'
```

- Times are original recording seconds or `HH:MM:SS.mmm` (up to three decimal
  places). They are not relative to a transcript chunk. The requested range must
  lie inside the recording; only added context is clipped at its edges. The final
  interval, including context, must be at most 30 seconds. JSON reports requested
  and actual start/end times plus duration and selected streams. If a container
  starts at a nonzero timestamp, explicit audio/video stream timing must establish
  its extent; ambiguous metadata is rejected instead of admitting ranges past
  the end as silence. Times use the container origin normalized to zero.
- Audio streams are zero-based among audio streams, not all container streams.
  The default is stream 0. Repeat the option to mix up to four distinct streams
  with equal weight; delays and early endings remain silence on the same source
  timeline. The result is mono 16-bit, 16 kHz PCM. A track is not a speaker.
- Choose exactly one destination: `--play` uses the configured `player`
  executable (`afplay` or `ffplay`) synchronously; `--output` creates a new `.wav`
  and returns its path for a local/chat player. Its parent must already exist.
  Existing files and symlinks are never replaced. The caller owns saved clips
  and removes them when finished; they are audio, not review receipts.
- Temporary audio is under the OS temporary directory, at most about 4.8 MB
  for four streams plus their mix. A 2 GiB disk reserve is required. Temporary
  files are cleaned on success, failure or Ctrl-C; no archive cleanup is done.
  Probe has a 30-second deadline, each extraction/mix 60 seconds, and playback
  the clip duration plus five seconds. Failure stops subprocess groups and
  returns `1`; Ctrl-C returns `130`. A forced process kill or system crash can
  leave its OS temporary directory behind.
- For iCloud, add `--fetch`; only the exact source goes to `icmat`. No session
  scan or fetch of transcript/receipt files is needed. Fetch has its existing
  20-minute materialization deadline and proves point-in-time readability only.

The local workspace file retains schema `1` and string values. Set `player` to
an executable path such as `/usr/bin/afplay`, with no shell arguments; absent a
setting, afplay then FFplay is found on PATH. `--config PATH` before `passage`
selects a different workspace file. Preparing a saved WAV needs no player.
Playback returning success establishes that the player exited successfully,
not that a human heard or verified the words.

Existing draft/review integrity checks remain separate tools. Editable working
text is outside the protected machine-output hashes; authorized annotations do
not block ordinary reading or playback. A changed protected output still fails
its recorded integrity check.

Use [reading](reading.md) for canonical selection, registration recovery,
dispositions and guide updates. Follow [evidence](evidence.md#reading-transcripts)
for search, attribution, comments and source-check claims.

## Qualification

No experimental reviewer is qualified. [Models](models.md#qualification) owns
the reference-case format, exact-recipe gate, recorded limitations and bounded
research steps. [Evidence](evidence.md#experimental-audio-review) owns the claims
that accepted audio-consuming coverage can establish.

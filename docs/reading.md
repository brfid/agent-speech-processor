# Reading folders and current transcripts

A promoted interview has a plain folder containing `README.md` and one
`Transcript*.md` per recording. Read and edit those transcripts. The README owns
supplied descriptions, title/date evidence, review scope and open questions.
Historical processing jobs stay where they are; their `transcript-edited.md`
files become preserved snapshots rather than competing working copies.

Promotion preserves text; it does not infer roles or verify speech. Where archive
policy requires contextual roles, follow the
[completion protocol](agents.md#completing-a-processing-request) before handoff.
The generated `Unclear speakers` seed reports review pending. The normative
[speech/role preservation contract](evidence.md#speech-and-editorial-preservation)
applies to current working text.

## Promote a completed session

Use an exact session manifest whose completion has already been established.
Promotion prepares reading copies without running recognition, reading audio or
rechecking machine completion. Choose a new, empty reading folder:

```sh
agent-speech-processor promote --session '/absolute/session/session.json' \
  --output '/absolute/reading/Descriptive interview' \
  --notes '/absolute/prepared-interview-notes.md' --fetch
```

`--notes` is optional prepared README Markdown, copied verbatim on the first
publication. Without it, the command supplies basic transcript and history links.
Add `--fetch` for iCloud controls/text; only exact selected files are fetched,
never original audio or models. Promotion creates `Transcript.md` for one
recording, or numbered transcripts for several. Explicitly unusable text retains
an `Unusable` filename label and a prominent notice. A supplied README must also
explain that exclusion prominently and link to its assessment.

Only the current generated edited preface followed by its current pending-role
note is accepted and shortened. Added introductory notes after that prefix,
timecoded wording and HTML annotations survive. Supported relative Markdown
link destinations are rebased and each changed span is recorded. Unknown
prefaces or ambiguous links stop for inspection. Exact prior text, notes and
manifest bytes remain in hidden promotion evidence as well as their original
locations. Machine baselines, raw results and historical receipts are unchanged.

The new hidden `.session.json` retains schema `1`, adds `reading_layout: 1`, and
keeps each recording's original identity, source hash, draft path, recipe hash and
audio streams. It adds `working_transcript`, relative to this reading folder.
Each job's `.working-transcript.json` points to that manifest using a relative
path. These small controls identify the current text; the README remains the
descriptive authority. They are not a catalog or an annotation store.

Publication stages exact bytes before registering the jobs. Repeat the same
command after interruption. Changed inputs or unpublished outputs stop for
reconciliation; preserve the staged files. After handoff, reruns preserve edits
to the current transcripts and README. Missing registered text requires
restoration, never regeneration or fallback to the historical snapshot. Do not
move only the reading folder without explicitly reconciling its relative
registrations; moving the archive as a unit preserves the paths.

## Read and search

Use `agent-speech-processor reading` with the new manifest or the original session manifest; job
registrations lead both to current text. Repeat `--session` for an explicit,
bounded set. With no session flag, the configured active session is used.

```sh
agent-speech-processor reading --session '/absolute/reading/Descriptive interview/.session.json'
agent-speech-processor reading --session '/absolute/reading/Descriptive interview/.session.json' \
  --notes '/absolute/additional-context/README.md' \
  --query 'packet' --query 'switching' --fetch
```

Default discovery includes the current reading README and canonical transcripts.
It excludes historical transcript copies, raw chunks and old job notes. Select
additional notes explicitly with `--notes` when they are relevant. Each included
file is read to EOF. Registration and selected-manifest controls are checked;
original identity hashes and audio streams are recorded values, not freshly
verified. Reading requires no audio, recognition model, processing receipt or
review qualification. It never compares user-owned text to an initial seed hash.

Reading requires a canonical manifest and agreeing job registration. An
unregistered completed session needs promotion before reading. Conflicting mappings,
repeated originals, shared job folders or shared canonical text are rejected.

The historical recording README still owns the explicit
`Reading disposition: unusable` field. Reading consults it even after promotion,
while keeping those historical notes out of default search. Unusable transcripts
remain preserved and linked but are not fetched or searched as current text.
Put the assessment, date and recovery restrictions beside the field, outside any
generated guide, and explain the exclusion in the current reading README. Only
`unusable` and `unspecified` are accepted; absence means unspecified, never
verified or usable. Free prose and transcript annotations are not parsed for
quality judgments. This disposition does not alter historical completion.
Promotion and role-readiness results use this current policy too; the original
promotion ledger retains its historical disposition evidence. A role packet pins
the policy file's presence and bytes so a changed exclusion cannot silently alter
the reviewed selection during publication.

Search uses literal, case-insensitive OR terms. Repeat `--query` for spelling and
sound-alike variants. `--context` controls surrounding lines (0–20); `--limit`
bounds returned matching lines and reports truncation. Results distinguish notes
from current working text. Raw HTML annotations remain visible. An annotation
is not speech, and a question is not an interviewee's claim. See
[evidence](evidence.md). For playback, use the original source, time range and
audio streams returned by the reading selection.

Machine completion and current text availability are separate.
`draft_completion.machine_draft_complete` reports recorded recognition coverage;
`working_transcript_present` reports metadata availability at the registered
location. Mapping errors or absent canonical text are reported separately and
make overall completion false without rewriting historical receipts. This
availability check neither reads/hashes the editable transcript nor verifies
speech. The standalone `verify` operation still checks its recorded machine
inputs/outputs; it is not a prerequisite for ordinary reading.

## Add readable navigation

```sh
agent-speech-processor reading --session '/absolute/reading/Descriptive interview/.session.json' \
  --update-guides --fetch
```

This updates a compact recording table in the current reading README only.
The table follows declared recording order, not historical chronology.
Links are relative; the promoted table keeps processing details out of the
reading path and labels unusable recordings.

Existing prose remains outside the marked region. Exact prior notes are saved
under `.reading-history/`; `.reading-guide.json` owns only the generated section's
hash and selected-manifest provenance. Publication uses a writer lock and prepared
checkpoint. Interrupted updates can resume; edited generated text or damaged
markers stop for reconciliation. The [current markers](workflow.md#current-formats-and-resume)
identify the managed region. Human prose and canonical transcripts remain
editable. The guide is navigation, not a new descriptive or quality authority.

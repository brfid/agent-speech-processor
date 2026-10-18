# Processing and reading interviews with an agent

This is the complete manager/worker/reviewer operating protocol for the toolkit.
Any agent permitted to read local files and run commands can use it. The software
does not call an LLM or require a provider, API key, SDK, MCP server, database or
background service. Archive-local instructions and records own authorization,
selected sources, destinations, editorial requirements and progress.

## Economical delegation

One lead owns the user's request, exact source set, authoritative records,
integration, verification and final report. Spawn helpers proactively at any
reasoning level when the likely speed or quality benefit exceeds context transfer,
duplicate reading, review and integration costs. Keep the original scope and
completion standard intact.

Choose the lowest-cost capable model and reasoning effort. A frontier-model lead
should consider a balanced helper when it can check the judgment independently.
With a frontier or balanced lead, explicitly consider GPT-6 Luna (Luna6) for clear
assignments whose likely errors and important omissions can be checked, such as
exact-list inventories, locator audits, receipt comparisons or synthetic test
adaptation. Keep ambiguous, consequential or difficult-to-check work with the lead
or a stronger capable model. Use deterministic tools for repeatable counts,
hashes, path checks and bulk transformations.

Each assignment specifies the exact task and source/time scope, relevant context
and authoritative records, exclusions, constraints, expected artifact, success
criteria and stopping boundary. Use disjoint write ownership: one writer per job,
recording, canonical transcript or shared document. If another worker takes over a
stage, transfer ownership explicitly. Reviewers remain read-only and report their
checked scope, method, evidence, discrepancies and limits.

Reuse completed work and existing artifacts before repeating analysis. Pass links,
compact extracts, structured packets and concise handoffs instead of broad history
or transcript dumps, with enough context to check the result. Keep repeated-task
prompts stable, version reusable prompts with the code they support and identify
varying inputs. Check results in proportion to the consequences and how easily
errors can hide. The lead integrates corrections and owns every completion claim.
Never reduce source coverage, provenance, evidence retention or required review
to save time or tokens.

This policy follows the division of responsibility in official OpenAI
[orchestration and handoffs](https://developers.openai.com/api/docs/guides/agents/orchestration),
[model selection](https://developers.openai.com/api/docs/guides/model-selection)
and [prompting](https://developers.openai.com/api/docs/guides/prompting) guidance.
The Luna6 preference and archival completion requirements are local project
policy; those sources do not mandate this toolkit or its model assignments.

## Bound the intake

Before choosing or processing an archive recording:

1. Read the archive instructions/status, selected collection/session guide,
   manifests and review queue. Declare exact recordings or the bounded search
   area, derivative destination, exclusions and stopping point. A singular
   recording request stays singular unless the user expands it.
2. Locate originals and supporting documents only within that bound. Check
   relevant sibling names and documentation ZIP member lists using metadata first.
   Keep organized outputs, receipts and existing jobs out of original-source counts.
   Fetch only exact needed files, never the entire archive.
3. Reconcile media with existing manifests, receipts and current transcript
   mappings. Inspect existing text before selecting unfinished work. Preserve
   unusable dispositions and recovery restrictions. A missing registered working
   transcript requires restoration, not a new draft or historical fallback.
4. Record exact source-relative paths, original names, sizes, modification times
   and dispositions: selected, already processed, excluded or unavailable. Hash
   readable selected media. Identify ZIP sources by container and member path;
   retain supporting documents, duplicates, supplied order and uncertain grouping.
5. Select only authorized unfinished files. Discovery does not authorize siblings,
   reruns or a different model. Record searched locations and access gaps; bounded
   coverage is not proof of historical completeness.

For selected ZIP media, preserve the ZIP and extract only selected members into
the derivative destination. Retain member names, bytes, timestamps and container
relationships. Scans use a scan-processing toolkit separately; this toolkit
processes audio recordings.

## Assign and run the work

The lead verifies exact source identities, variants and output locations.
Extraction workers may own disjoint selected recordings and their required role
stage. A read-only reviewer checks source accounting, raw/baseline preservation,
resume state, required editorial completion and stated gaps. Never change
recognition settings, rewrite receipts or omit a stage to make parallel work fit.

Follow [first drafting](quickstart.md#4-draft-one-recording) and
[recovery](workflow.md#resume-and-failures). Inspect selected audio streams before
recognition; a track is not a person. Preserve originals and existing drafts,
notes, failed attempts and receipts. Change recognition inputs/settings only in
an authorized new variant. Reuse completed/prepared responses rather than rerun
recognition unnecessarily.

## Completing a processing request

Read the archive's completion policy. `draft`, `verify` and `promote` perform
mechanical stages; none infers contextual roles. Where the archive requires a
text-only role pass, normal processing includes it without a separate relabeling
request. Honor an explicit user instruction limited to raw extraction.

After verifying machine completion, [promote the exact session](reading.md#promote-a-completed-session)
into its reading folder. The registered `Transcript*.md` becomes current working
text; the old edited file stays a snapshot. For required roles, use:

```sh
agent-speech-processor roles --session '/absolute/reading/.session.json' \
  --output '/absolute/role-decisions.json'
# Read the whole packet, then fill method and every role without changing other fields.
agent-speech-processor roles --session '/absolute/reading/.session.json' \
  --decisions '/absolute/role-decisions.json'
agent-speech-processor roles --session '/absolute/reading/.session.json' --check
agent-speech-processor reading --session '/absolute/reading/.session.json' --update-guides
```

Add `--fetch` to each command for selected iCloud text/controls, including the
locked recheck; these operations never fetch audio. The packet contains every
timed block, full raw Markdown/annotations, current guides and pinned selection/
policy controls, including their absence. Read every block sequentially with
adjacent exchanges and annotations. Assign only contextually supported
**Interviewer** or **Interviewee** roles; use **Unclear speakers** for uncertainty
or mixed-role blocks. Punctuation alone, track numbers and participant lists do
not establish roles, voice identities or exact turns.

The tool validates full coverage and unchanged context, applies only labels and
the qualifying role note, and stages exact before/after evidence before resumable
publication. Stale packets, missing blocks or changed speech stop the operation.
Export a fresh packet after text, guides, registration or disposition changes and
read the revised context. Preserve failed-attempt evidence.

Follow the normative [speech and role preservation contract](evidence.md#speech-and-editorial-preservation):
reversing labels and restoring the exact original role-status note, including any
pending note, must reproduce the prior working text byte for byte. Preserve all
other wording, timecodes, block boundaries, links, annotations and line endings.
Keep protected baselines, snapshots, raw results and receipts unchanged.
`roles --check` returns exit 2 while review is pending; require exit 0 before a
normal role-complete handoff. This checks format/readiness, not attribution
quality or listening. Machine completion and audio checking remain separate.

## Hand off the session

Before declaring the bounded request complete:

1. Run `agent-speech-processor verify` for each selected job. Require
   `byte_integrity: true` and `draft_completion.machine_draft_complete: true` for
   completed machine drafting. Inspect the exact session, compare source metadata
   to intake and account for every selected original as outputs or explicit failure.
2. Confirm one registered current transcript per completed readable recording.
   Require `roles --check` for the archive-required contextual pass. Check retained
   baselines/raw results, historical snapshots, exact role-edit preservation and
   existing user edits. Keep these evidence claims distinct.
3. Update the current guide with source/document links, supplied date evidence,
   review method/date, exact coverage, role counts and unresolved questions. Check
   reading selection and local links. Preserve explicit unusable dispositions.
4. Update collection status and browse indexes with exact sources, verified
   outputs, failures, gaps and next action. Session guides own descriptive evidence;
   receipts own mechanical state. Keep private records outside this repository.
5. Report completed scope and direct reading links. If incomplete, report usable
   outputs, retained retry state and the exact resume action. Stop at the boundary.

## Read or edit a selected transcript

Use `agent-speech-processor reading --session '/absolute/session.json'` to select
current guides and text. Repeat `--query` for discovery variants or add explicit
`--notes`; see [reading](reading.md#read-and-search) for selection behavior.
Reading requires canonical registration. Promote an unregistered completed
session first; a missing registered file is a recovery error.

Follow the complete [reading and attribution rules](evidence.md#reading-transcripts):
read raw Markdown including HTML annotations, search plausible spelling/sound
variants, inspect surrounding questions/answers, retain qualifications and unknown
speakers, cite original time ranges and separate wording, notes and inference.
For consequential uncertainty, prepare a bounded source passage using the recorded
stream selection and [playback rules](workflow.md#reading-and-playback).

Edit only authorized current working text. Record the basis for substantive edits
and notes, preserve uncertainty and source times, and retain the accepted result.
Editorial work does not establish audio checking. The
[fictional example](../examples/reading-example.md) illustrates these boundaries.

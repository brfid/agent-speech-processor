# Fictional reading example

This is an invented illustration of the transcript format, not recognizer output
or a record of anyone speaking. It has no corresponding source recording.

The following illustrates a current `Transcript.md` after a text-only role pass.
Before promotion, an unregistered session uses `transcript-edited.md`.

```markdown
# Transcript

Recording: `demo.wav`. Speaker identities and exact turns remain unverified.
Times: positions in the original recording. Audio-checked coverage: none.

Roles are inferred from text, not checked against audio. **Interviewer** and
**Interviewee** are provisional roles, not verified identities. **Unclear speakers**
marks uncertain attribution or a block that may contain more than one role.
Existing blocks and timestamps are retained; labels do not establish exact speaker turns.

[00:00:02.000–00:00:03.500] **Interviewer:** Was the system deployed that summer?

[00:00:04.000–00:00:09.000] **Interviewee:** Only a small trial took place that summer. It was not a full deployment.

<!-- Interpretation at 00:00:04.000–00:00:09.000:
The passage distinguishes a trial from deployment. This note is based on the
text; the exact wording and speaker still require listening to the source.
-->
```

An agent asked whether deployment happened that summer should preserve the
denial, read surrounding passages, and distinguish the text from its note.
An unsuccessful search for “deployment” should lead to variants such as
“deploy”, “rollout”, “roll out”, and “trial”; it does not establish absence.

With a real recording, the next check would be:

```sh
agent-speech-processor passage --source '/absolute/demo.wav' --start 4 --end 9 --play
```

Follow [evidence](../docs/evidence.md#reading-transcripts) for the complete reading protocol.

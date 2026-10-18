# Software status

Updated 2026-10-17. The sole command is `agent-speech-processor`; Python imports
use `agent_speech_processor`.

Sequential local drafting, bounded playback, registered reading folders and
contextual role-review packets are implemented. Originals, raw recognition,
protected baselines, receipts and user edits are preserved. Mechanical completion,
text-inferred roles and audio checking remain separate claims. No experimental
audio reviewer has passed reference qualification.

Defaults are 120-second cores, five seconds of audio context, zero recognizer text
context, CPU recognition and temporary WAVs. Use the
[current formats and resume rules](docs/workflow.md#current-formats-and-resume).

The candidate passed **209 synthetic tests** through normal macOS access on
2026-10-17, including preservation, interrupted resume,
current-format processing and unsupported-format rejection. These checks do not
establish speech accuracy or listening.

Source/wheel builds and clean base-install workflows passed, including 68
installed synthetic workflow checks and source installation in a path with spaces.
Hosted macOS/Linux and package CI checks pushed commits; confirm its result for
the selected release. Follow [releasing](docs/releasing.md). Archive progress
belongs in archive records.

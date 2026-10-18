# Models and local runtime

## Supported drafting path

Install whisper.cpp separately and pass an explicit compatible ggml model to
`agent-speech-processor draft --model PATH`. The
[quickstart](quickstart.md#3-install-whisper-separately) gives upstream-linked
source-build and small-model smoke-test steps. This toolkit does not install a
recognition engine or download Whisper weights. Keep tools/models outside Git.

CPU is the default; `--gpu` opts into the installed engine's GPU backend. Model
quality, speed and resource needs depend on the material, model and runtime.
A successful smoke test is not a speech-accuracy benchmark. Job recipes retain
exact model/tool/environment/code identities. Changed recognition dependencies
or settings need a new output variant; see
[current formats and resume](workflow.md#current-formats-and-resume).

Plan memory for the model, inference state and operating system together. Do not
assume a larger model or concurrent recognizers will fit because its weight file
fits on disk. New chunks reserve at least 2 GiB beyond their bounded temporary and
publication allocation. Inspect actual elapsed times and accuracy on representative
passages before trading model size, chunk length, decoding settings or runtime for
speed. Longer chunks change boundaries and work lost before a checkpoint; removing
overlap or integrity checks is not an acceptable speed shortcut. Keep performance
measurements outside recipe identity.

Reading/search requires no model. Passage extraction/playback requires audio tools
and optionally a player, without loading recognition or review weights.

## Experimental review research

The optional `models` and `gemma` extras support Apple-silicon research, outside the
supported base installation. No reviewer has passed reference qualification.
Normal setup does not install these extras or weights. Commands are grouped under
`agent-speech-processor experimental`: `install-model`, `review` and `evaluate`.
Help loads no optional packages. The installer downloads only the explicitly
selected experimental model; it does not install Whisper.

Before an experiment, explicitly select its model/revision, source scope, license,
resource budget and stopping rule. Do not download weights or corpora merely
because the software scaffold is present. Installation receipts pin every file and
verify published LFS checksums when available. Inference uses offline Hugging Face/
Transformers settings; only explicit installation performs network downloads.
Keep weights, media, installation receipts and trial artifacts outside Git.

`requirements-models.lock` records the experimental Python runtime pins. The
recorded trial stack used `mlx-audio 0.5.6`, `mlx-lm 0.31.3`, `mlx 0.32.2`,
`transformers 5.17.0` and, for Gemma, `mlx-vlm 0.7.3`. These are pinned implementation
inputs, not a claim of qualification or current upstream recommendations.

## Recorded synthetic trials

The following bounded observations date to 2026-09-26. Only synthetic speech was
submitted; no interview accuracy was measured. They identify audio-path and
structured-output failures to preserve in future experiments, without supplying
portable performance or memory estimates. The experimental installer requires
actual download size plus an 8 GiB disk reserve; working clips retain a 2 GiB reserve.

| Backend | Pinned revision | License | Recorded download bytes |
| --- | --- | --- | ---: |
| MOSS | `OpenMOSS-Team/MOSS-Transcribe-Diarize` @ `704aa4a9c304e8520be88901e0d1960158ef5b15` | Apache-2.0 | 1,833,074,504 |
| Qwen2-Audio | `mlx-community/Qwen2-Audio-7B-Instruct-4bit` @ `c65570002626f41b4dc08b7b54f42f99f3e82e7f` | Apache-2.0 | 6,574,658,472 |
| Gemma 3n | `mlx-community/gemma-3n-E2B-it-4bit` @ `66e7276cfc589073b4f92ebed58c05301f990222` | Gemma terms | 4,502,728,123 |

- MOSS transcribed a short synthetic recording with one anonymous speaker. Its
  installer needed `chat_template.jinja`; the corrected installer includes and
  hashes it. That single case does not qualify diarization or correction.
- Given candidate text saying “four” and audio saying “three,” Qwen2-Audio repeated
  the wrong draft word. It also
  returned Python-literal output or copied the schema instead of requested JSON.
  Simpler prompts did not resolve the wrong word.
- Gemma 3n returned empty text on the same structured single-audio task. Its
  processor does not advertise multi-audio
  support; the recorded MLX-VLM path would silently retain only the first view.
  The wrapper rejects that condition before making a coverage claim.

These are diagnostic observations, not corpus benchmarks or portable throughput
estimates. Neither recognizer agreement nor successful structured output proves
that a reviewer used the audio correctly. External tools/models keep their own
licenses; this repository's MIT license does not relicense them.

Sources: [MOSS Python implementation](https://github.com/Blaizzy/mlx-audio/tree/main/mlx_audio/stt/models/moss_transcribe_diarize),
[Qwen2-Audio interface](https://github.com/Blaizzy/mlx-audio/blob/main/mlx_audio/stt/models/qwen2_audio/README.md),
[MLX-VLM](https://github.com/Blaizzy/mlx-vlm),
[Gemma terms](https://ai.google.dev/gemma/terms).

## Qualification

Keep reviewer research bounded. First diagnose the recorded audio path and output
failures with synthetic inputs; compare audio-only transcription with correction
without conflating them. Do not suppress multi-audio guards to obtain a successful
return code. Freeze a viable audio-consuming recipe before held-out evaluation;
do not tune against final references. Speaker continuity/embedding work follows
only when the review method works and evidence shows it is needed.

`agent-speech-processor experimental evaluate --references PATH --whisper-model PATH --output PATH`
uses the same reviewer/diarizer options as `review`. Reference JSON has schema `1`
and at least three `cases`, each with `id`, `source`, `source_sha256`,
`audio_streams` and timed `reference` segments (`start`, `end`, `text`, `speaker`).
Each case must fit one core. Include a multi-speaker case and a
`"challenge": true` case with a deliberately wrong draft word.

Before evaluation, independently check reference wording, intervals and anonymous
speaker labels against the actual source audio. Record who checked which spans,
the method and unresolved reference uncertainty. Do not treat candidate ASR/LLM
output or recognizer agreement as ground truth. Freeze the checked reference bytes
and source identities before evaluation, keep them separate from prompt tuning,
and retain their provenance. Reference wording never enters the reviewer prompt.
The evaluator checks format and computed gates; it does not establish reference
quality or replace that source check.

The implemented gate requires valid calls, no worse word or speaker error in any
case and improvement on each deliberate challenge. Local Whisper is the baseline;
MOSS contributes anonymous labels by time overlap. Retain raw outputs, word error
and permutation-invariant concatenated speaker word error. Before accepting a
method for wider archive use, require representative multi-speaker, constructed
overlap, missing-speech and silence checks beyond the fixed short cases. A small
constructed evaluation can reject a poor reviewer but cannot
establish historical-interview accuracy.

Only an exact qualified recipe and complete accepted audio-consuming coverage may
publish an automated reviewed transcript. Failed/unreviewed spans remain gaps;
qualification does not establish human listening. If the method remains unreliable,
retain machine-draft status and document the blocker. Do not substitute recognizer
consensus or a billed service for actual qualified audio review. Publication
mechanics live in [workflow](workflow.md#experimental-review); evidence boundaries
live in [evidence](evidence.md#experimental-audio-review).

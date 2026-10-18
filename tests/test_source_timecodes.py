"""Recording-relative locators and preservation with disposable recognizer output."""
import argparse
import array
import contextlib
import io
import json
import math
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from agent_speech_processor.processes import run
from agent_speech_processor.experimental.render import transcript
from agent_speech_processor.audio import audio_input_command
from agent_speech_processor.whisper import process
from agent_speech_processor.timestamps import timecode


class SourceTimecodes(unittest.TestCase):
    def test_rounding_carries_across_minutes_and_hours(self):
        for seconds, expected in ((0, "00:00:00.000"), (59.9996, "00:01:00.000"),
                                  (3599.9996, "01:00:00.000"),
                                  (3601.125, "01:00:01.125"),
                                  (360000, "100:00:00.000")):
            with self.subTest(seconds=seconds):
                self.assertEqual(timecode(seconds), expected)
        for seconds in (-1, float("nan"), float("inf"), True, "1"):
            with self.subTest(seconds=seconds), self.assertRaises(ValueError):
                timecode(seconds)

    def test_review_offsets_are_applied_once_to_words_and_uncertainty(self):
        span = dict(index=180, start=3598, end=3606, core_start=3600, core_end=3604)
        report = dict(publishable=False,
                      coverage=dict(reviewed_seconds=4, duration_seconds=3606),
                      windows=[dict(span=span, accepted=True,
                                    review=dict(segments=[dict(start=1.5, end=3.125,
                                                               speaker="unknown", text="Synthetic words",
                                                               uncertain=True)],
                                                unresolved=[dict(start=3.125, end=4,
                                                                 reason="Unclear synthetic word")]))])
        model = dict(model_id="synthetic", revision="test")
        result = transcript(Path("synthetic.wav"), report, dict(reviewer=model, diarizer=model))
        self.assertIn("## 01:00:00.000–01:00:04.000", result)
        self.assertIn("[00:59:59.500–01:00:01.125] **Unknown speaker:** Synthetic words [uncertain]", result)
        self.assertIn("[01:00:01.125–01:00:02.000] [Uncertain: Unclear synthetic word]", result)

    def test_draft_retains_overlap_and_notes_survive_regeneration_attempt(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, model, engine = (root / name for name in ("synthetic.wav", "model", "whisper-cli"))
            for path in (source, model, engine):
                path.write_bytes(b"disposable fixture")
            output = root / "draft"
            args = argparse.Namespace(source=source, model=model, output=output, chunk_seconds=3600,
                                      language="en", gpu=False, fetch=False, limit_chunks=None)
            command_count = 0
            chunk_start = 0
            clip_seconds = 0

            def fake_run(command, **kwargs):
                nonlocal command_count, chunk_start, clip_seconds
                command_count += 1
                if command[0] == "ffprobe":
                    info = dict(format=dict(duration="3606"),
                                streams=[dict(codec_type="audio", duration="3606", channels=1)])
                    return subprocess.CompletedProcess(command, 0, json.dumps(info).encode(), b"")
                if command[0] == "ffmpeg":
                    chunk_start = command[command.index("-ss") + 1]
                    clip_seconds = command[command.index("-t") + 1]
                    Path(command[-1]).write_bytes(b"synthetic chunk audio")
                else:
                    # Both chunks recognize the same original interval across the hour.
                    local_start = 3599.5 - chunk_start
                    local_end = 3600.25 - chunk_start
                    record = {"transcription": [{"offsets": {"from": round(local_start * 1000),
                                                               "to": round(local_end * 1000)},
                                                 "text": " repeated boundary words "}]}
                    prefix = Path(command[command.index("-of") + 1])
                    prefix.with_suffix(".json").write_text(json.dumps(record))
                    prefix.with_suffix(".srt").write_text("synthetic local SRT")
                    prefix.with_suffix(".txt").write_text("repeated boundary words")
                return subprocess.CompletedProcess(command, 0, b"", b"")

            original = source.read_bytes()
            with patch("agent_speech_processor.whisper.shutil.which", return_value=str(engine)), \
                    patch("agent_speech_processor.whisper.run", side_effect=fake_run), \
                    patch("agent_speech_processor.whisper.recipe", return_value=(
                        {"tools": {"whisper-cli": {"sha256": "synthetic"}},
                         "model": {"sha256": "synthetic", "bytes": 1}, "environment": {},
                         "draft_code_sha256": {"whisper.py": "synthetic"}}, b"fixture")), \
                    patch("agent_speech_processor.whisper.pcm_identity", side_effect=lambda unused: {"duration_seconds": clip_seconds}), \
                    contextlib.redirect_stdout(io.StringIO()):
                result = process(args)
                self.assertTrue(result["machine_draft_complete"])
                draft = output / "transcript-draft.md"
                text = draft.read_text()
                self.assertIn("## 00:59:55.000–01:00:06.000", text)
                self.assertEqual(text.count("[00:59:59.500–01:00:00.250] repeated boundary words"), 2)
                self.assertIn("repeated speech is intentionally retained", text)
                receipt = json.loads((output / ".processing.json").read_text())
                self.assertEqual(receipt["specification"]["settings"]["adapter_version"], 4)
                before_calls = command_count
                process(args)
                self.assertEqual(command_count, before_calls)
                self.assertEqual(draft.read_text(), text)
                self.assertEqual(json.loads((output / ".processing.json").read_text())["steps"],
                                 receipt["steps"])
                annotated = text + "\n<!-- Synthetic research note; no listening claimed. -->\n"
                draft.write_text(annotated)
                with self.assertRaisesRegex(ValueError, "modified"):
                    process(args)
                self.assertEqual(draft.read_text(), annotated)
                self.assertEqual(command_count, before_calls)
                # An old recipe must stop before rewriting even its receipt.
                old_receipt = json.loads((output / ".processing.json").read_text())
                old_receipt["specification"]["settings"]["adapter_version"] = 1
                del old_receipt["specification"]["settings"]["audio_timeline"]
                ledger = output / ".processing.json"
                ledger.write_text(json.dumps(old_receipt))
                historical_receipt = ledger.read_bytes()
                with self.assertRaisesRegex(ValueError, "Unsupported Whisper draft receipt"):
                    process(args)
                self.assertEqual(ledger.read_bytes(), historical_receipt)
                self.assertEqual(draft.read_text(), annotated)
                self.assertEqual(command_count, before_calls)
            self.assertEqual(source.read_bytes(), original)


class DraftTrackTiming(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.source = self.root / "delayed.mov"
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=4",
             "-itsoffset", "1.25", "-f", "lavfi", "-i", "sine=frequency=880:duration=2.5",
             "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le", self.source])
        self.original = self.source.read_bytes()

    def test_nonzero_chunk_preserves_delayed_single_and_mixed_tracks(self):
        for indices in ([1], [0, 1]):
            with self.subTest(indices=indices):
                clip = self.root / ("clip-" + "".join(map(str, indices)) + ".wav")
                run(audio_input_command(self.source, 0.5, 2.0, indices, clip))
                with wave.open(str(clip)) as sound:
                    self.assertEqual((sound.getframerate(), sound.getnchannels()), (16000, 1))
                    self.assertEqual(sound.getnframes(), 24000)
                    samples = array.array("h", sound.readframes(sound.getnframes()))

                def strength(frequency, start, end):
                    section = samples[round(start * 16000):round(end * 16000)]
                    real = sum(x * math.cos(2 * math.pi * frequency * i / 16000)
                               for i, x in enumerate(section)) / len(section)
                    imaginary = sum(x * math.sin(2 * math.pi * frequency * i / 16000)
                                    for i, x in enumerate(section)) / len(section)
                    return math.hypot(real, imaginary)

                # The delayed voice starts 0.75s into this nonzero source window.
                self.assertLess(strength(880, 0.1, 0.3), 1)
                self.assertGreater(strength(880, 0.9, 1.1), 900)
                if len(indices) == 2:
                    self.assertGreater(strength(440, 0.1, 0.3), 900)
                    self.assertGreater(strength(440, 0.9, 1.1), 900)
                else:
                    self.assertLess(strength(440, 0.1, 0.3), 1)
                self.assertEqual(self.source.read_bytes(), self.original)

    def test_draft_uses_recording_duration_when_only_delayed_track_is_selected(self):
        model, engine = self.root / "model", self.root / "whisper-cli"
        for path in (model, engine):
            path.write_bytes(b"disposable fixture")
        output = self.root / "draft"
        args = argparse.Namespace(source=self.source, model=model, output=output, chunk_seconds=600,
                                  language="en", gpu=False, fetch=False, limit_chunks=None,
                                  audio_stream_indices=[1], keep_audio=True)

        def fake_recognizer(command, **kwargs):
            if str(command[0]) != str(engine):
                return run(command, **kwargs)
            record = {"transcription": [{"offsets": {"from": 3000, "to": 3500},
                                         "text": "synthetic late-track tail"}]}
            prefix = Path(command[command.index("-of") + 1])
            prefix.with_suffix(".json").write_text(json.dumps(record))
            prefix.with_suffix(".srt").write_text("synthetic local SRT")
            prefix.with_suffix(".txt").write_text("synthetic late-track tail")
            return subprocess.CompletedProcess(command, 0, b"", b"")

        with patch("agent_speech_processor.whisper.shutil.which", return_value=str(engine)), \
                patch("agent_speech_processor.whisper.run", side_effect=fake_recognizer), \
                patch("agent_speech_processor.whisper.recipe", return_value=(
                    {"tools": {"whisper-cli": {"sha256": "synthetic"}},
                     "model": {"sha256": "synthetic", "bytes": 1}, "environment": {},
                     "draft_code_sha256": {"whisper.py": "synthetic"}}, b"fixture")), \
                contextlib.redirect_stdout(io.StringIO()):
            process(args)
        receipt = json.loads((output / ".processing.json").read_text())
        self.assertEqual(receipt["steps"]["media-probe"]["details"]["duration"], 4)
        self.assertIn("[00:00:03.000–00:00:03.500] synthetic late-track tail",
                      (output / "transcript-draft.md").read_text())
        with wave.open(str(next((output / "chunks").glob("*.wav")))) as sound:
            self.assertEqual(sound.getnframes(), 64000)
        self.assertEqual(self.source.read_bytes(), self.original)

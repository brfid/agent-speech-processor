"""Bounded local playback with disposable media, never an audio device or interview."""
import array
from contextlib import redirect_stderr, redirect_stdout
import io
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from agent_speech_processor import cli, passage
from agent_speech_processor.records import atomic, checksum
from agent_speech_processor.timestamps import parse_time


class PassageRanges(unittest.TestCase):
    def test_source_timecodes_cross_hour_without_reapplying_chunk_offset(self):
        self.assertEqual(parse_time("600.125"), 600.125)
        self.assertEqual(parse_time("00:59:59.750"), 3599.75)
        self.assertEqual(parse_time("01:00:00.250"), 3600.25)
        span = passage.source_range(3599.75, 3600.25, 0.5, 4000)
        self.assertEqual((span["start"], span["end"]), (3599.25, 3600.75))

    def test_context_clamps_at_both_recording_edges(self):
        first = passage.source_range(0.1, 0.3, 0.5, 3)
        last = passage.source_range(2.7, 2.9, 0.5, 3)
        self.assertEqual((first["start"], first["end"]), (0, 0.8))
        self.assertEqual((last["start"], last["end"]), (2.2, 3))

    def test_bad_time_text_is_rejected_instead_of_normalized(self):
        for value in ("nan", "NaN", "inf", "-inf", "-1", "", "1:02",
                      "00:60:00.000", "00:00:60.000", "01:-1:01", "words",
                      "9" * 309 + ":00:00"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                parse_time(value)

    def test_invalid_ranges_context_and_duration_are_rejected(self):
        cases = [(-1, 1, 0, 3), (1, 1, 0, 3), (2, 1, 0, 3), (0, 4, 0, 3),
                 (0, 1, -1, 3), (0, 1, 0, 0), (0, 1, 0, -1)]
        for index in range(4):
            for nonfinite in (float("nan"), float("inf"), -float("inf")):
                values = [0, 1, 0, 3]
                values[index] = nonfinite
                cases.append(tuple(values))
        for values in cases:
            with self.subTest(values=values), self.assertRaises(ValueError):
                passage.source_range(*values)

    def test_context_cannot_expand_a_clip_beyond_thirty_seconds(self):
        for values in ((0, 30.001, 0, 100), (10, 30, 6, 100)):
            with self.subTest(values=values), self.assertRaises(ValueError):
                passage.source_range(*values)
        span = passage.source_range(0, 30, 0, 100)
        self.assertEqual((span["start"], span["end"]), (0, 30))
        span = passage.source_range(2.002, 32.002, 0, 100)
        self.assertAlmostEqual(span["end"] - span["start"], 30)


class PassageCommand(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name).resolve()
        self.source = self.root / "original recording.wav"
        # Silent first/last seconds make source-offset errors audible in the samples.
        samples = array.array("h", [0] * 16000)
        samples.extend(round(6000 * math.sin(2 * math.pi * 440 * i / 16000)) for i in range(16000))
        samples.extend([0] * 16000)
        if sys.byteorder != "little":
            samples.byteswap()
        with wave.open(str(self.source), "wb") as sound:
            sound.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            sound.writeframes(samples.tobytes())
        self.config = self.root / "workspace.json"
        self.config.write_text(json.dumps({"schema": 1}))
        self.output = self.root / "passage.wav"
        self.player_log = self.root / "player-observation.json"

    def invoke(self, *options, start="1.25", end="1.75", source=None):
        arguments = ["--config", str(self.config), "passage", "--source", str(source or self.source),
                     "--start", start, "--end", end, *map(str, options)]
        stdout, stderr = io.StringIO(), io.StringIO()
        with redirect_stdout(stdout), redirect_stderr(stderr):
            code = cli.main(arguments)
        return code, json.loads(stdout.getvalue()) if stdout.getvalue() else None, stderr.getvalue()

    def fake_player(self, exit_code=0):
        folder = self.root / "player tools"
        folder.mkdir(exist_ok=True)
        player = folder / "ffplay"
        player.write_text(
            f"#!{sys.executable}\n"
            "import json, pathlib, sys, wave\n"
            "clip = pathlib.Path(sys.argv[-1])\n"
            "with wave.open(str(clip)) as audio:\n"
            "    seconds = audio.getnframes() / audio.getframerate()\n"
            f"pathlib.Path({str(self.player_log)!r}).write_text(json.dumps("
            "{'arguments': sys.argv[1:], 'clip': str(clip), 'seconds': seconds}))\n"
            f"sys.stderr.write('synthetic player diagnostic\\n' if {exit_code} else '')\n"
            f"sys.exit({exit_code})\n"
        )
        player.chmod(0o700)
        self.config.write_text(json.dumps({"schema": 1, "player": str(player),
                                          "active_session": str(self.root / "absent-session.json")}))
        return player

    def test_prepare_reports_actual_source_interval_and_preserves_research_files(self):
        transcript = self.root / "transcript-draft.md"
        transcript.write_text("[00:00:01.250 – 00:00:01.750] Machine words.\n"
                              "<!-- Human note: uncertain term, listen again. -->\n")
        receipt = self.root / ".processing.json"
        receipt.write_text("An intentionally stale receipt must never gate playback.\n")
        before = {path: path.read_bytes() for path in (self.source, transcript, receipt, self.config)}
        code, result, error = self.invoke("--output", self.output)
        self.assertEqual((code, error), (0, ""))
        self.assertEqual(result["source"], str(self.source))
        self.assertEqual(result["requested"], {"start": 1.25, "end": 1.75,
                                              "start_timecode": "00:00:01.250", "end_timecode": "00:00:01.750"})
        self.assertEqual(result["actual"], result["requested"])
        self.assertEqual(result["audio_stream_indices"], [0])
        self.assertEqual(result["duration_seconds"], 0.5)
        self.assertEqual(result["clip_path"], str(self.output))
        self.assertFalse(result["played"])
        with wave.open(str(self.output)) as clip:
            self.assertEqual((clip.getnchannels(), clip.getsampwidth(), clip.getframerate()), (1, 2, 16000))
            self.assertEqual(clip.getnframes(), 8000)
            self.assertNotEqual(set(clip.readframes(8000)), {0})
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents, str(path))
        self.assertEqual(set(self.root.iterdir()), {*before, self.output})
        self.assertIn("<!-- Human note:", transcript.read_text())

    def test_requested_timecodes_and_clamped_context_remain_distinct(self):
        code, result, error = self.invoke("--context-seconds", "0.5", "--output", self.output,
                                          start="00:00:00.100", end="00:00:00.300")
        self.assertEqual((code, error), (0, ""))
        self.assertEqual((result["requested"]["start"], result["requested"]["end"]), (0.1, 0.3))
        self.assertEqual((result["actual"]["start"], result["actual"]["end"]), (0, 0.8))
        self.assertEqual(result["actual"]["start_timecode"], "00:00:00.000")
        self.assertEqual(result["duration_seconds"], 0.8)
        with wave.open(str(self.output)) as clip:
            self.assertEqual(set(clip.readframes(clip.getnframes())), {0})

    def test_invalid_cli_ranges_create_no_output(self):
        for start, end, extra in (("1", "1", []), ("2", "1", []), ("0", "4", []),
                                  ("nan", "1", []), ("0", "inf", []),
                                  ("0", "1", ["--context-seconds", "nan"]),
                                  ("0", "1", ["--context-seconds", "-1"])):
            with self.subTest(start=start, end=end, extra=extra):
                code, result, error = self.invoke("--output", self.output, *extra, start=start, end=end)
                self.assertNotEqual(code, 0)
                self.assertIsNone(result)
                self.assertTrue(error)
                self.assertFalse(self.output.exists())

    def test_exactly_one_delivery_mode_is_required(self):
        for extra in ([], ["--play", "--output", str(self.output)]):
            with self.subTest(extra=extra), self.assertRaises(SystemExit) as raised:
                self.invoke(*extra)
            self.assertEqual(raised.exception.code, 2)

    def test_missing_duplicate_and_negative_streams_leave_no_output(self):
        for indices in ((1,), (0, 0), (-1,)):
            options = [part for index in indices for part in ("--audio-stream-index", str(index))]
            with self.subTest(indices=indices):
                code, result, error = self.invoke("--output", self.output, *options)
                self.assertNotEqual(code, 0)
                self.assertIsNone(result)
                self.assertTrue(error)
                self.assertFalse(self.output.exists())

    def test_existing_output_and_original_cannot_be_overwritten(self):
        self.output.write_bytes(b"existing clip or human material")
        for output in (self.output, self.source):
            with self.subTest(output=output):
                before = output.read_bytes()
                code, _, error = self.invoke("--output", output)
                self.assertNotEqual(code, 0)
                self.assertTrue(error)
                self.assertEqual(output.read_bytes(), before)

    def test_output_created_while_extracting_is_not_overwritten(self):
        contents = b"Created by another process after the initial collision check."

        def concurrent_create(path, data, **options):
            self.output.write_bytes(contents)
            return atomic(path, data, **options)

        with patch("agent_speech_processor.passage.atomic", side_effect=concurrent_create):
            code, result, error = self.invoke("--output", self.output)
        self.assertNotEqual(code, 0)
        self.assertIsNone(result)
        self.assertTrue(error)
        self.assertEqual(self.output.read_bytes(), contents)
        self.assertFalse(list(self.root.glob(".pending-*")))

    def test_output_symlinks_are_preserved_including_dangling_links(self):
        for target in (self.source, self.root / "missing-original.wav"):
            with self.subTest(target=target):
                self.output.symlink_to(target)
                source_before = checksum(self.source)
                code, _, error = self.invoke("--output", self.output)
                self.assertNotEqual(code, 0)
                self.assertTrue(error)
                self.assertTrue(self.output.is_symlink())
                self.assertEqual(self.output.readlink(), target)
                self.assertEqual(checksum(self.source), source_before)
                if target != self.source:
                    self.assertFalse(target.exists())
                self.output.unlink()

    def test_symlink_source_and_missing_output_parent_are_rejected(self):
        alias = self.root / "source-alias.wav"
        alias.symlink_to(self.source)
        code, _, error = self.invoke("--output", self.output, source=alias)
        self.assertNotEqual(code, 0)
        self.assertTrue(error)
        self.assertFalse(self.output.exists())
        absent = self.root / "missing-directory"
        code, _, error = self.invoke("--output", absent / "passage.wav")
        self.assertNotEqual(code, 0)
        self.assertTrue(error)
        self.assertFalse(absent.exists())

    def test_fetch_selects_only_exact_source_and_creates_no_receipts(self):
        before = set(self.root.iterdir())
        with patch("agent_speech_processor.inputs.fetch", return_value=({"success": True}, True)) as fetch:
            code, _, error = self.invoke("--fetch", "--output", self.output)
        self.assertEqual((code, error), (0, ""))
        fetch.assert_called_once_with([self.source])
        self.assertEqual(set(self.root.iterdir()), before | {self.output})

    def test_failed_fetch_stops_before_creating_output(self):
        with patch("agent_speech_processor.inputs.fetch", return_value=({"success": False}, False)) as fetch:
            code, _, error = self.invoke("--fetch", "--output", self.output)
        self.assertNotEqual(code, 0)
        self.assertIn("icmat", error)
        fetch.assert_called_once_with([self.source])
        self.assertFalse(self.output.exists())

    def test_interrupted_extraction_cleans_partial_audio_without_publishing(self):
        partial = []
        source_before = checksum(self.source)

        def interrupt(source, span, indices, folder):
            clip = folder / "partial.wav"
            clip.write_bytes(b"incomplete synthetic audio")
            partial.append(clip)
            raise KeyboardInterrupt()

        with patch("agent_speech_processor.passage.prepare_clip", side_effect=interrupt):
            code, result, error = self.invoke("--output", self.output)
        self.assertEqual(code, 130)
        self.assertIsNone(result)
        self.assertTrue(error)
        self.assertFalse(self.output.exists())
        self.assertEqual(len(partial), 1)
        self.assertFalse(partial[0].exists())
        self.assertEqual(checksum(self.source), source_before)

    def test_explicit_player_config_plays_then_removes_temporary_clip(self):
        self.fake_player()
        source_before = checksum(self.source)
        code, result, error = self.invoke("--play")
        self.assertEqual((code, error), (0, ""))
        observation = json.loads(self.player_log.read_text())
        self.assertEqual(observation["seconds"], 0.5)
        self.assertIn("-autoexit", observation["arguments"])
        self.assertIn("-nodisp", observation["arguments"])
        self.assertFalse(Path(observation["clip"]).exists())
        self.assertIsNone(result["clip_path"])
        self.assertTrue(result["played"])
        self.assertEqual(checksum(self.source), source_before)
        self.assertFalse((self.root / "absent-session.json").exists())

    def test_player_failure_reports_diagnostic_and_removes_temporary_clip(self):
        self.fake_player(exit_code=7)
        code, result, error = self.invoke("--play")
        self.assertNotEqual(code, 0)
        self.assertIsNone(result)
        self.assertIn("synthetic player diagnostic", error)
        observation = json.loads(self.player_log.read_text())
        self.assertFalse(Path(observation["clip"]).exists())

    def test_player_timeout_and_cancellation_are_bounded_and_cleanup(self):
        self.fake_player()
        for failure, expected_code in ((subprocess.TimeoutExpired("ffplay", 1), 1),
                                       (KeyboardInterrupt(), 130)):
            clips = []

            def interrupted(arguments, *, timeout, **unused):
                self.assertGreater(timeout, 0)
                self.assertLessEqual(timeout, 60)
                clip = Path(arguments[-1])
                self.assertTrue(clip.is_file())
                clips.append(clip)
                raise failure

            with self.subTest(failure=type(failure).__name__):
                with patch("agent_speech_processor.passage.run", side_effect=interrupted):
                    code, result, error = self.invoke("--play")
                self.assertEqual(code, expected_code)
                self.assertIsNone(result)
                self.assertTrue(error)
                self.assertEqual(len(clips), 1)
                self.assertFalse(clips[0].exists())

    def test_player_setting_is_an_executable_not_a_shell_command(self):
        marker = self.root / "must-not-be-created"
        self.config.write_text(json.dumps({"schema": 1, "player": f"ffplay; touch {marker}"}))
        code, result, error = self.invoke("--play")
        self.assertNotEqual(code, 0)
        self.assertIsNone(result)
        self.assertTrue(error)
        self.assertFalse(marker.exists())

    def test_relative_player_path_does_not_depend_on_working_directory(self):
        self.config.write_text(json.dumps({"schema": 1, "player": "./ffplay"}))
        code, result, error = self.invoke("--play")
        self.assertEqual(code, 1)
        self.assertIsNone(result)
        self.assertIn("absolute", error)


if __name__ == "__main__":
    unittest.main()

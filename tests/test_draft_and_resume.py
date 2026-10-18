import argparse
import array
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
import wave
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from agent_speech_processor.job import job
from agent_speech_processor.records import checksum, atomic
from agent_speech_processor.inputs import prepare_sources, fetch
from agent_speech_processor.processes import run
from agent_speech_processor.drafts import windows, segments
from agent_speech_processor.timestamps import timecode
from agent_speech_processor.audio import audio_input_command


class SafetyAndResume(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "original.txt"
        self.source.write_text("Original record.\n")
        self.output = self.root / "derivative"
        self.calls = 0

    def build(self, tmp):
        self.calls += 1
        (tmp / "result.txt").write_text("Extracted text")
        return {"pages/result.txt": tmp / "result.txt"}, {"checked": False}

    def execute(self):
        with job(self.output, "test", [self.source], {}, False) as current:
            current.step("page", self.build)

    def test_resume_skips_completed_work_and_preserves_source(self):
        before = checksum(self.source)
        self.execute()
        self.execute()
        self.assertEqual(self.calls, 1)
        self.assertEqual(checksum(self.source), before)

    def test_missing_or_modified_output_is_not_silently_overwritten(self):
        self.execute()
        (self.output / "pages/result.txt").write_text("Human correction")
        with self.assertRaisesRegex(ValueError, "modified"):
            self.execute()
        self.assertEqual((self.output / "pages/result.txt").read_text(), "Human correction")

    def test_untracked_output_is_never_overwritten(self):
        with job(self.output, "test", [self.source], {}, False) as current:
            target = self.output / "pages/result.txt"
            target.parent.mkdir()
            target.write_text("An existing unrelated file")
            with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
                current.step("page", self.build)
            self.assertEqual(target.read_text(), "An existing unrelated file")

    def interrupt_after_output_publication(self):
        def interrupted(path, data, **options):
            atomic(path, data, **options)
            if Path(path).name == "result.txt":
                raise KeyboardInterrupt()
        with patch("agent_speech_processor.job.atomic", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.execute()

    def test_resume_after_interrupted_output_publication(self):
        self.interrupt_after_output_publication()
        before = checksum(self.output / "pages/result.txt")
        self.execute()
        self.assertEqual(checksum(self.output / "pages/result.txt"), before)
        self.assertEqual(self.calls, 1)

    def test_modified_incomplete_output_is_never_overwritten(self):
        self.interrupt_after_output_publication()
        target = self.output / "pages/result.txt"
        target.write_text("Human correction after interruption")
        with self.assertRaisesRegex(ValueError, "refusing to overwrite"):
            self.execute()
        self.assertEqual(target.read_text(), "Human correction after interruption")

    def test_atomic_creation_preserves_existing_file(self):
        with self.assertRaises(FileExistsError):
            atomic(self.source, b"Replacement", replace=False)
        self.assertEqual(self.source.read_text(), "Original record.\n")

    def test_changed_source_rejects_stale_receipt(self):
        self.execute()
        self.source.write_text("Changed source")
        with self.assertRaisesRegex(ValueError, "Source bytes changed"):
            self.execute()

    def test_interrupted_step_resumes(self):
        def interrupted(tmp):
            (tmp / "partial").write_text("incomplete")
            raise KeyboardInterrupt()
        with self.assertRaises(KeyboardInterrupt):
            with job(self.output, "test", [self.source], {}, False) as current:
                current.step("page", interrupted)
        state = json.loads((self.output / ".processing.json").read_text())
        self.assertEqual(state["steps"]["page"]["status"], "failed")
        self.execute()
        state = json.loads((self.output / ".processing.json").read_text())
        self.assertEqual(state["steps"]["page"]["attempts"], 2)

    def test_concurrent_writer_rejected(self):
        with job(self.output, "test", [self.source], {}, False):
            with self.assertRaisesRegex(RuntimeError, "active writer"):
                with job(self.output, "test", [self.source], {}, False):
                    pass

    def test_source_cannot_be_inside_output_folder(self):
        with self.assertRaisesRegex(ValueError, "original source"):
            with job(self.root, "test", [self.source], {}, False):
                pass

    def test_duplicate_inputs_rejected(self):
        with self.assertRaises(ValueError):
            prepare_sources([self.source, self.source])

    def test_parent_alias_cannot_hide_duplicate_or_icloud_input(self):
        (self.root / "alias").symlink_to(self.root, target_is_directory=True)
        with self.assertRaises(ValueError):
            prepare_sources([self.source, self.root / "alias" / self.source.name])
        cloud = self.root / "com~apple~CloudDocs"
        cloud.mkdir()
        (cloud / "original.txt").write_text("fixture")
        (self.root / "cloud-alias").symlink_to(cloud, target_is_directory=True)
        sources = prepare_sources([self.root / "cloud-alias/original.txt"])
        with self.assertRaisesRegex(ValueError, "Use --fetch"):
            with job(self.output, "test", sources, {}, False):
                self.fail("A parent alias must not bypass icmat")

    def test_shared_job_checks_source_on_exit(self):
        with self.assertRaisesRegex(RuntimeError, "Original changed"):
            with job(self.output, "test", [self.source], {}, False) as current:
                current.step("page", self.build)
                self.source.write_text("Changed while the adapter was running")

    def test_path_traversal_and_symlink_escape_rejected(self):
        with job(self.output, "test", [self.source], {}, False) as current:
            with self.assertRaises(ValueError):
                current.output_path("../original.txt")
            (self.output / "escape").symlink_to(self.root, target_is_directory=True)
            with self.assertRaises(ValueError):
                current.output_path("escape/original.txt")

    def test_readme_corrections_survive(self):
        self.output.mkdir()
        (self.output / "README.md").write_text("Human metadata and evidence")
        with job(self.output, "test", [self.source], {}, False) as current:
            current.write_readme("Generated content")
        self.assertEqual((self.output / "README.md").read_text(), "Human metadata and evidence")

    def test_folder_rename_preserves_resume(self):
        self.execute()
        renamed = self.root / "Better document title"
        self.output.rename(renamed)
        self.output = renamed
        self.execute()
        self.assertEqual(self.calls, 1)

    def test_recipe_change_requires_separate_variant(self):
        self.execute()
        with self.assertRaisesRegex(ValueError, "Inputs/settings differ"):
            with job(self.output, "test", [self.source], {"different": True}, False):
                pass

    def test_incomplete_icmat_stops_before_processing(self):
        with patch("agent_speech_processor.job.fetch", return_value=({"success": False}, False)):
            with self.assertRaisesRegex(RuntimeError, "icmat"):
                with job(self.output, "test", [self.source], {}, True):
                    self.fail("Must not process after incomplete materialization")
        self.assertTrue((self.output / "icmat-receipt.json").exists())

    def test_external_command_timeout_is_bounded(self):
        with self.assertRaises(subprocess.TimeoutExpired):
            run([sys.executable, "-c", "import time; time.sleep(10)"], timeout=0.05)

    def test_timeout_stops_children_after_parent_has_exited(self):
        marker = self.root / "surviving-child.txt"
        child = ("import pathlib,time; time.sleep(0.5); "
                 f"pathlib.Path({str(marker)!r}).write_text('still running')")
        parent = f"import subprocess,sys; subprocess.Popen([sys.executable, '-c', {child!r}])"
        with self.assertRaises(subprocess.TimeoutExpired):
            run([sys.executable, "-c", parent], timeout=0.15)
        time.sleep(0.6)
        self.assertFalse(marker.exists(), "A child process escaped timeout cleanup")

    def test_external_command_failure_preserves_diagnostic(self):
        with self.assertRaisesRegex(RuntimeError, "expected diagnostic"):
            run([sys.executable, "-c", "import sys; print('expected diagnostic',file=sys.stderr); sys.exit(2)"])

    def test_icmat_failure_receipt_uses_shared_command_runner(self):
        receipt = {"success": True, "enumerationComplete": True, "failures": [],
                   "counts": {"filesVerified": 1, "regularFiles": 1, "datalessFiles": 0}}
        with patch("agent_speech_processor.inputs.run", return_value=subprocess.CompletedProcess(
                [], 1, json.dumps(receipt).encode(), b"failure")) as command:
            saved, ok = fetch([self.source])
        self.assertEqual(saved, receipt)
        self.assertFalse(ok)
        self.assertFalse(command.call_args.kwargs["check"])

    def test_icloud_source_requires_installed_icmat_route(self):
        source = self.root / "com~apple~CloudDocs" / "original.txt"
        source.parent.mkdir()
        source.write_text("fixture")
        with self.assertRaisesRegex(ValueError, "Use --fetch"):
            with job(self.output, "test", [source], {}, False):
                self.fail("iCloud input must go through icmat")


class AudioLocators(unittest.TestCase):
    def test_separate_audio_tracks_both_survive_mono_mix(self):
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "two microphones.mov"
            mixed = Path(directory) / "mixed.wav"
            run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=1",
                 "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
                 "-map", "0:a:0", "-map", "1:a:0", "-c:a", "pcm_s16le", source])
            run(audio_input_command(source, 0, 1, [0, 1], mixed))
            with wave.open(str(mixed)) as sound:
                self.assertEqual((sound.getframerate(), sound.getnchannels()), (16000, 1))
                samples = array.array("h", sound.readframes(sound.getnframes()))

            def strength(frequency):
                count = 8000
                real = sum(samples[i] * math.cos(2 * math.pi * frequency * i / 16000)
                           for i in range(count)) / count
                imaginary = sum(samples[i] * math.sin(2 * math.pi * frequency * i / 16000)
                                for i in range(count)) / count
                return math.hypot(real, imaginary)

            self.assertGreater(strength(440), 200)
            self.assertGreater(strength(880), 200)

    def test_chunk_coverage_and_overlap(self):
        spans = windows(1250.25, 600, 5)
        self.assertEqual(spans, [(0, 605), (595, 1205), (1195, 1250.25)])
        self.assertTrue(all(b[0] <= a[1] for a, b in zip(spans, spans[1:])))

    def test_timestamps_are_milliseconds(self):
        record = {"transcription": [{"offsets": {"from": 1250, "to": 3000}, "text": " words "}]}
        self.assertEqual(segments(record, 10), [(1.25, 3, "words")])
        self.assertEqual(timecode(3601.25), "01:00:01.250")

    def test_invalid_timestamps_and_duration_rejected(self):
        with self.assertRaises(ValueError):
            segments({"transcription": [{"offsets": {"from": 100, "to": 9000}, "text": "x"}]}, 2)
        with self.assertRaises(ValueError):
            windows(float("nan"), 600, 5)

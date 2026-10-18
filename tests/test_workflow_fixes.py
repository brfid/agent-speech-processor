"""The four observed workflow failures, using disposable source and response data."""
import contextlib
import copy
import io
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import wave

from test_editorial_pipeline import DraftFixture
from agent_speech_processor.cli import dispatch, parser
from agent_speech_processor.records import atomic, checksum
from agent_speech_processor.records import digest
from agent_speech_processor.drafts import import_draft
from agent_speech_processor.provenance import DRAFT_MODULES, code_snapshot, draft_code_identity
from agent_speech_processor.transcripts import edited_text, trim_boundary
from agent_speech_processor.whisper import process, recognize


class OverlapEvidence(unittest.TestCase):
    def item(self, start, end, text):
        return dict(start=start, end=end, text=text, note="")

    def render(self, left, right):
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            chunks = []
            originals = {}
            for index, (start, end, items) in enumerate(((0, 125, left), (115, 240, right))):
                name = f"{index}.json"
                raw = json.dumps({"transcription": [
                    {"offsets": {"from": round((i['start'] - start) * 1000),
                                 "to": round((i['end'] - start) * 1000)}, "text": i["text"]}
                    for i in items]})
                (folder / name).write_text(raw)
                originals[name] = raw
                chunks.append(dict(start_seconds=start, end_seconds=end, json=name))
            result = edited_text(folder / "source.wav", folder, 240, [0], chunks, "synthetic")
            for name, raw in originals.items():
                self.assertEqual((folder / name).read_text(), raw)
            return result

    def test_signs_symbols_and_numeric_forms_remain_distinct_readings(self):
        for first, second in (("-5", "5"), ("−5", "5"), ("+5", "5"), ("$5", "5"),
                              ("5.0", "50"), ("5%", "5"), ("1:5", "1.5")):
            with self.subTest(first=first, second=second):
                left = [self.item(115, 122, f"The balance was {first} million.")]
                right = [self.item(115, 122, f"The balance was {second} million.")]
                text, summary = self.render(left, right)
                self.assertIn(left[0]["text"], text)
                self.assertIn(right[0]["text"], text)
                self.assertEqual(summary, dict(removed_overlap_words=0, unresolved_boundaries=1))
                self.assertIn("> Overlapping recognition alternative", text)

    def test_matching_prefix_does_not_hide_overlapping_contradiction(self):
        left = [self.item(115, 122, "No, only in the trial.")]
        right = [self.item(115, 125, "No, only in the trial. It was used everywhere.")]
        text, summary = self.render(left, right)
        self.assertEqual(summary, dict(removed_overlap_words=5, unresolved_boundaries=1))
        self.assertIn("No, only in the trial.", text)
        self.assertIn("It was used everywhere.", text)
        self.assertIn("> Overlapping recognition alternative", text)
        self.assertIn("00:01:55.000–00:02:02.000", text)
        self.assertIn("[00:01:55.000–00:02:05.000]", text)

    def test_broad_timing_does_not_erase_genuine_repetition(self):
        for left, right in (
                ([self.item(110, 122, "Earlier speech. We did it again today.")],
                 [self.item(115, 125, "We did it again today.")]),
                ([self.item(115, 122, "We did it again today.")],
                 [self.item(120, 125, "We did it again today.")]),
                ([self.item(115, 122, "We did it again today.")],
                 [self.item(115, 125, "We did it again today.")])):
            with self.subTest(left=left, right=right):
                self.assertEqual(trim_boundary(left, right, 115, 125), (right, 0))
                text, summary = self.render(left, right)
                self.assertEqual(text.count("We did it again today."), 2)
                self.assertEqual(summary["unresolved_boundaries"], 1)

    def test_ordinary_duplicate_context_removed_with_later_speech_preserved(self):
        left = [self.item(115, 122, "No, only in the trial.")]
        right = [self.item(115, 122, "No, only in the trial."),
                 self.item(123, 130, "Later speech outside the preceding recognition.")]
        text, summary = self.render(left, right)
        self.assertEqual(summary, dict(removed_overlap_words=5, unresolved_boundaries=0))
        self.assertEqual(text.count("No, only in the trial."), 1)
        self.assertIn(right[1]["text"], text)


class DraftWorkflow(DraftFixture, unittest.TestCase):
    def verify(self):
        with patch("agent_speech_processor.cli.workspace", return_value={}):
            return dispatch(parser().parse_args(["verify", "--output", str(self.folder)]))

    def two_chunks(self):
        with wave.open(str(self.source), "wb") as sound:
            sound.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            sound.writeframes(b"\0\0" * 16000 * 30)
        self.args.chunk_seconds = 20

    def failed_step(self):
        return next(s for s in self.receipt()["steps"].values() if s["status"] == "failed")

    def without_recognition_resources(self):
        self.args.model = None
        self.model.unlink()
        self.engine.unlink()
        with contextlib.ExitStack() as stack:
            for name in ("run", "recipe", "check_space", "shutil.which"):
                stack.enter_context(patch("agent_speech_processor.whisper." + name,
                                          side_effect=AssertionError("Recognition resource accessed: " + name)))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            return process(self.args)

    def test_malformed_json_and_prior_failed_attempts_survive_successful_retry(self):
        stages = []
        for raw in (b'{"transcription": [', b'not JSON from a second attempt'):
            with self.assertRaises(ValueError):
                self.run_draft(raw=raw, diagnostic=b"Recognizer returned malformed synthetic JSON")
            step = self.failed_step()
            stage = self.folder / step["staging"]
            stages.append((stage, raw))
            self.assertTrue(step["rejected"])
            self.assertEqual(step["outputs"], {})
            diagnostic = json.loads((stage / "recognition.json").read_text())
            self.assertIn("malformed synthetic JSON", diagnostic["stderr"])
            report, code = self.verify()
            self.assertEqual(code, 2)
            self.assertTrue(report["byte_integrity"])
            self.assertFalse(report["verified"])
            self.assertEqual(report["draft_completion"]["completed_chunks"], 0)
            self.assertFalse(list(self.folder.rglob("*.wav")))
        self.run_draft()
        chunk = next(s for s in self.receipt()["steps"].values() if "json" in s.get("details", {}))
        self.assertEqual(len(chunk["failed_attempts"]), 2)
        for stage, raw in stages:
            self.assertEqual((stage / "draft.json").read_bytes(), raw)
        self.assertEqual(self.calls, 3)
        self.assertTrue(self.verify()[0]["draft_completion"]["complete"])

    def test_invalid_timestamps_are_retained_but_never_accepted(self):
        for index, (start, end) in enumerate(((0, 9000), (-1, 1000), (0, 0), (1000, 500),
                                              (float("nan"), 1000), (True, 1000))):
            with self.subTest(start=start, end=end):
                self.folder = self.root / f"invalid-{index}"
                self.args.output = self.folder
                raw = json.dumps({"transcription": [{"offsets": {"from": start, "to": end}, "text": "synthetic"}]}).encode()
                with self.assertRaises(ValueError):
                    self.run_draft(raw=raw)
                step = self.failed_step()
                self.assertEqual((self.folder / step["staging"] / "draft.json").read_bytes(), raw)
                self.assertEqual(step["outputs"], {})
                self.assertFalse((self.folder / "transcript-draft.md").exists())
                self.assertEqual(self.verify()[0]["draft_completion"]["completed_chunks"], 0)

    def test_malformed_response_structure_cannot_be_accepted_as_empty_speech(self):
        for index, value in enumerate(({"transcription": {}}, {"transcription": None},
                                        {"transcription": [{"offsets": {"from": 0, "to": 1000}, "text": None}]})):
            with self.subTest(value=value):
                self.folder = self.root / f"bad-structure-{index}"
                self.args.output = self.folder
                raw = json.dumps(value).encode()
                with self.assertRaises(ValueError):
                    self.run_draft(raw=raw)
                step = self.failed_step()
                self.assertTrue(step["rejected"])
                self.assertEqual((self.folder / step["staging"] / "draft.json").read_bytes(), raw)
                self.assertEqual(self.verify()[0]["draft_completion"]["completed_chunks"], 0)

    def test_failed_command_retains_partial_output_and_bounded_diagnostics(self):
        raw = b'{"partial recognizer output":'
        with self.assertRaisesRegex(RuntimeError, "Recognizer failed \(17\)"):
            self.run_draft(raw=raw, exit_code=17, diagnostic=b"x" * 20000 + b" useful failure reason")
        step = self.failed_step()
        stage = self.folder / step["staging"]
        self.assertFalse(step.get("prepared"))
        self.assertEqual((stage / "draft.json").read_bytes(), raw)
        diagnostic = json.loads((stage / "recognition.json").read_text())
        self.assertEqual(diagnostic["returncode"], 17)
        self.assertIn("useful failure reason", diagnostic["stderr"])
        self.assertTrue(diagnostic["stderr_truncated"])
        self.assertLessEqual(len(diagnostic["stderr"]), 16384)
        wav = Path(diagnostic["command"][diagnostic["command"].index("-f") + 1])
        self.assertFalse(wav.exists())
        self.run_draft()
        self.assertEqual((stage / "draft.json").read_bytes(), raw)
        self.assertTrue((self.folder / "diagnostics").is_dir())
        self.assertEqual(self.calls, 2)

    def test_interruption_before_validation_reuses_prepared_response_without_resources(self):
        with patch("agent_speech_processor.whisper.validate_recognition", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_draft()
        step = self.failed_step()
        self.assertTrue(step["prepared"])
        self.assertFalse(step.get("rejected"))
        self.assertEqual(step["outputs"], {})
        self.assertTrue((self.folder / step["staging"] / "draft.json").is_file())
        self.assertFalse(list(self.folder.rglob("*.wav")))
        result = self.without_recognition_resources()
        self.assertTrue(result["draft_completion"]["complete"])
        self.assertEqual(self.calls, 1)

    def test_interrupted_publication_reuses_prepared_response_without_resources(self):
        def interrupted(path, data, **options):
            atomic(path, data, **options)
            if Path(path).parent.name == "chunks" and Path(path).suffix == ".json":
                raise KeyboardInterrupt()
        with patch("agent_speech_processor.job.atomic", side_effect=interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.run_draft()
        self.assertTrue(self.without_recognition_resources()["draft_completion"]["complete"])
        self.assertEqual(self.calls, 1)

    def test_changed_prepared_response_stops_without_another_recognition_call(self):
        with patch("agent_speech_processor.whisper.validate_recognition", side_effect=KeyboardInterrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_draft()
        step = self.failed_step()
        path = self.folder / step["staging"] / step["prepared"][step["details"]["json"]]["file"]
        path.write_bytes(b"changed response")
        with self.assertRaisesRegex(ValueError, "Prepared output changed"):
            self.without_recognition_resources()
        self.assertEqual(path.read_bytes(), b"changed response")
        self.assertEqual(self.calls, 1)

    def test_bounded_draft_reports_integrity_and_completion_separately(self):
        self.two_chunks()
        self.args.limit_chunks = 1
        result = self.run_draft()
        report, code = self.verify()
        self.assertEqual(code, 0)  # Historical verify exit code for a bounded draft.
        self.assertTrue(report["verified"])
        self.assertTrue(report["byte_integrity"])
        self.assertEqual(report["incomplete_steps"], [])
        completion = report["draft_completion"]
        self.assertEqual((completion["completed_chunks"], completion["expected_chunks"]), (1, 2))
        self.assertFalse(completion["complete"])
        self.assertFalse(completion["combined_baseline_present"])
        self.assertFalse(completion["edited_transcript_present"])
        self.assertEqual(result["draft_completion"], completion)

    def test_failed_second_chunk_reports_incomplete_coverage(self):
        self.two_chunks()
        self.args.limit_chunks = 1
        self.run_draft()
        with self.assertRaises(ValueError):
            self.run_draft(raw=b"malformed second chunk")
        report, code = self.verify()
        self.assertEqual(code, 2)
        self.assertTrue(report["byte_integrity"])
        self.assertFalse(report["verified"])
        self.assertEqual(len(report["incomplete_steps"]), 1)
        self.assertEqual(report["draft_completion"]["completed_chunks"], 1)
        self.assertEqual(report["draft_completion"]["expected_chunks"], 2)

    def test_missing_baseline_and_missing_edited_file_have_distinct_reports(self):
        self.run_draft()
        before = (self.folder / ".processing.json").read_bytes()
        baseline = self.folder / "transcript-draft.md"
        original = baseline.read_bytes()
        baseline.unlink()
        report, code = self.verify()
        self.assertEqual(code, 1)
        self.assertFalse(report["byte_integrity"])
        self.assertFalse(report["draft_completion"]["machine_draft_complete"])
        self.assertFalse(report["draft_completion"]["combined_baseline_present"])
        baseline.write_bytes(original)
        (self.folder / "transcript-edited.md").unlink()
        report, code = self.verify()
        self.assertEqual(code, 0)
        self.assertTrue(report["byte_integrity"])
        self.assertTrue(report["draft_completion"]["machine_draft_complete"])
        self.assertFalse(report["draft_completion"]["edited_transcript_present"])
        self.assertFalse(report["draft_completion"]["complete"])
        self.assertEqual((self.folder / ".processing.json").read_bytes(), before)

    def test_completed_reuse_needs_no_model_tools_or_chunk_disk_reserve(self):
        self.run_draft()
        edited = self.folder / "transcript-edited.md"
        edited.write_text("User-owned replacement with <!-- interpretation notes -->\n")
        before = {p: p.read_bytes() for p in (edited, self.folder / "transcript-draft.md",
                                             self.folder / "recipe.json", self.folder / "recipe-source.zip")}
        result = self.without_recognition_resources()
        self.assertTrue(result["draft_completion"]["complete"])
        self.assertTrue(self.verify()[0]["byte_integrity"])
        for path, contents in before.items():
            self.assertEqual(path.read_bytes(), contents)
        self.assertEqual(self.calls, 1)

    def test_unfinished_recognition_still_requires_model(self):
        self.two_chunks()
        self.args.limit_chunks = 1
        self.run_draft()
        before = (self.folder / ".processing.json").read_bytes()
        self.args.model = None
        with self.assertRaisesRegex(ValueError, "--model is required"):
            self.run_draft()
        self.assertEqual((self.folder / ".processing.json").read_bytes(), before)

    def snapshot_package(self, folder):
        hashes, self.snapshot = code_snapshot(folder)
        self.spec["code_sha256"] = hashes
        self.spec["draft_code_sha256"] = draft_code_identity(hashes)

    def test_playback_and_experimental_changes_allow_partial_resume_keep_full_provenance(self):
        package = self.root / "package"
        shutil.copytree(Path(__file__).resolve().parents[1] / "src/agent_speech_processor", package)
        self.snapshot_package(package)
        self.two_chunks()
        self.args.limit_chunks = 1
        self.run_draft()
        recorded = {name: (self.folder / name).read_bytes() for name in ("recipe.json", "recipe-source.zip")}
        full_identity = dict(self.spec["code_sha256"])
        for name in ("passage.py", "clips.py", "experimental/review.py", "experimental/worker.py", "transcripts.py", "timestamps.py", "cli.py"):
            path = package / name
            path.write_text(path.read_text() + "\n# Unrelated synthetic change.\n")
        path = package / "experimental/audio.py"
        path.write_text(path.read_text().replace("webrtcvad.Vad(2)", "webrtcvad.Vad(3)"))
        old_draft_identity = self.spec["draft_code_sha256"]
        self.snapshot_package(package)
        self.assertNotEqual(full_identity, self.spec["code_sha256"])
        self.assertEqual(old_draft_identity, self.spec["draft_code_sha256"])
        self.assertTrue(self.run_draft()["draft_completion"]["complete"])
        self.assertEqual(self.calls, 2)
        for name, content in recorded.items():
            self.assertEqual((self.folder / name).read_bytes(), content)

    def test_recognition_dependencies_require_new_variant_without_receipt_rewrite(self):
        self.two_chunks()
        self.args.limit_chunks = 1
        self.run_draft()
        original = copy.deepcopy(self.spec)
        before = (self.folder / ".processing.json").read_bytes()
        for field, value in (("model", {"sha256": "changed weights", "bytes": 1}),
                             ("tools", {"whisper-cli": {"sha256": "changed binary"}}),
                             ("tools", {**original["tools"], "ffmpeg": {"sha256": "changed extractor"}}),
                             ("environment", {"python": "changed runtime"}),
                             ("draft_code_sha256", {"whisper.py": "changed recognition code"})):
            with self.subTest(field=field, value=value):
                self.spec = copy.deepcopy(original)
                self.spec[field] = value
                with self.assertRaisesRegex(ValueError, "Inputs/settings differ"):
                    self.run_draft()
                self.assertEqual((self.folder / ".processing.json").read_bytes(), before)
        self.spec = original
        self.args.max_context = 10
        with self.assertRaisesRegex(ValueError, "Inputs/settings differ"):
            self.run_draft()
        self.assertEqual((self.folder / ".processing.json").read_bytes(), before)
        self.assertEqual(self.calls, 1)

    def test_duration_and_pcm_code_enter_the_compatibility_identity(self):
        package = self.root / "package"
        shutil.copytree(Path(__file__).resolve().parents[1] / "src/agent_speech_processor", package)
        self.snapshot_package(package)
        before = self.spec["draft_code_sha256"]
        path = package / "audio.py"
        original = path.read_text()
        for old, new in (('return media["duration"]', 'return media["duration"] + 1'),
                         ('RATE = 16000', 'RATE = 8000'),
                         ('return raw, frames / RATE', 'return raw, frames / (RATE + 1)')):
            with self.subTest(code=old):
                self.assertIn(old, original)
                path.write_text(original.replace(old, new))
                self.snapshot_package(package)
                self.assertNotEqual(before, self.spec["draft_code_sha256"])

    def test_every_pinned_module_change_rejects_partial_resume_without_rewriting_receipts(self):
        package = self.root / "package"
        shutil.copytree(Path(__file__).resolve().parents[1] / "src/agent_speech_processor", package)
        self.snapshot_package(package)
        self.two_chunks()
        self.args.limit_chunks = 1
        self.run_draft()
        before = (self.folder / ".processing.json").read_bytes()
        for name in DRAFT_MODULES:
            with self.subTest(module=name):
                path = package / name
                original = path.read_text()
                path.write_text(original + "\n# Changed pinned processing source.\n")
                self.snapshot_package(package)
                with self.assertRaisesRegex(ValueError, "Inputs/settings differ"):
                    self.run_draft()
                self.assertEqual((self.folder / ".processing.json").read_bytes(), before)
                path.write_text(original)
        self.assertEqual(self.calls, 1)

    def test_incomplete_recognition_snapshot_is_rejected(self):
        hashes, _ = code_snapshot()
        for name in DRAFT_MODULES:
            with self.subTest(module=name):
                incomplete = dict(hashes)
                del incomplete[name]
                with self.assertRaisesRegex(ValueError, "missing required modules"):
                    draft_code_identity(incomplete)

    def test_unsupported_audio_receipts_are_rejected_without_migration_or_recognition(self):
        from agent_speech_processor.retention import process as prune
        from argparse import Namespace
        self.run_draft()
        ledger = self.folder / '.processing.json'
        current = self.receipt()
        for change in ('schema', 'adapter', 'missing-adapter', 'recipe', 'missing-recipe'):
            with self.subTest(change=change):
                state = copy.deepcopy(current)
                settings = state['specification']['settings']
                if change == 'schema': state['schema'] = 1
                elif change == 'adapter': settings['adapter_version'] = 3
                elif change == 'missing-adapter': settings.pop('adapter_version')
                elif change == 'recipe': settings['recipe_version'] = 1
                else: settings.pop('recipe_version')
                ledger.write_text(json.dumps(state))
                before = ledger.read_bytes()
                for operation in (self.verify, self.run_draft,
                                  lambda: import_draft(self.folder, self.source, checksum(self.source), digest(settings)),
                                  lambda: prune(Namespace(output=self.folder, fetch=False, apply=False))):
                    with self.assertRaisesRegex(ValueError, 'Unsupported Whisper draft receipt'):
                        operation()
                    self.assertEqual(ledger.read_bytes(), before)
                self.assertEqual(self.calls, 1)

    def test_audio_recreation_rejects_unsupported_adapters(self):
        from agent_speech_processor.retention import extraction_command
        for adapter in (None, 1, 2, 3, 5):
            with self.subTest(adapter=adapter), self.assertRaisesRegex(ValueError, 'current draft adapter 4'):
                extraction_command(self.source, 0, 1, [0], self.root / 'recreated.wav', adapter)


class InterruptedDiagnostics(unittest.TestCase):
    def test_timeout_retains_partial_raw_output_and_process_diagnostics(self):
        with tempfile.TemporaryDirectory() as directory:
            stage = Path(directory)
            script = ("import pathlib, sys, time; "
                      f"pathlib.Path({str(stage / 'draft.json')!r}).write_bytes(b'partial raw output'); "
                      "print('partial stdout', flush=True); print('partial stderr', file=sys.stderr, flush=True); "
                      "time.sleep(10)")
            with self.assertRaises(subprocess.TimeoutExpired):
                recognize([sys.executable, "-c", script], stage, timeout=0.3)
            self.assertEqual((stage / "draft.json").read_bytes(), b"partial raw output")
            diagnostic = json.loads((stage / "recognition.json").read_text())
            self.assertIn("partial stdout", diagnostic["stdout"])
            self.assertIn("partial stderr", diagnostic["stderr"])
            self.assertIn("TimeoutExpired", diagnostic["error"])
            self.assertEqual(diagnostic["status"], "interrupted_or_failed")


if __name__ == "__main__":
    unittest.main()

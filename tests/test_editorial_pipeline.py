"""Preservation, replay, low-disk and editorial evidence boundaries."""
import argparse
import contextlib
import io
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch
import wave

from agent_speech_processor.cli import parser, dispatch
from agent_speech_processor.records import checksum, atomic
from agent_speech_processor.job import Job
from agent_speech_processor.verification import recorded_job
from agent_speech_processor.processes import run
from agent_speech_processor.records import digest
from agent_speech_processor.drafts import import_draft
from agent_speech_processor.passage import source_range
from agent_speech_processor.transcripts import edited_text, located_chunks, trim_boundary
from agent_speech_processor.whisper import process
from agent_speech_processor.audio import pcm_identity, audio_input_command
from agent_speech_processor.provenance import tool_identity


class DraftFixture:
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.wav"
        with wave.open(str(self.source), "wb") as sound:
            sound.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            sound.writeframes(b"\0\0" * 16000)
        self.model, self.engine = self.root / "model", self.root / "engine"
        self.model.write_bytes(b"synthetic model")
        self.engine.write_bytes(b"synthetic recognizer")
        self.folder = self.root / "draft"
        self.args = argparse.Namespace(source=self.source, output=self.folder, model=self.model,
                                       language="en", gpu=False, fetch=False, chunk_seconds=120,
                                       max_context=0, limit_chunks=None, audio_stream_indices=[0], keep_audio=False)
        self.calls = 0
        self.spec = {"schema": 2, "tools": {"whisper-cli": {"sha256": checksum(self.engine)}},
                     "model": {"sha256": checksum(self.model), "bytes": self.model.stat().st_size},
                     "environment": {"python": "synthetic"}, "draft_code_sha256": {"whisper.py": "synthetic"},
                     "code_sha256": {"whisper.py": "synthetic", "passage.py": "synthetic"}}

    def run_draft(self, *, raw=None, exit_code=0, diagnostic=b""):
        def recognize(command, **kwargs):
            if str(command[0]) != str(self.engine):
                return run(command, **kwargs)
            self.calls += 1
            prefix = Path(command[command.index("-of") + 1])
            prefix.with_suffix(".json").write_bytes(raw if raw is not None else json.dumps({"transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": "No, only in the trial."}]}).encode())
            prefix.with_suffix(".srt").write_text("synthetic srt")
            prefix.with_suffix(".txt").write_text("No, only in the trial.")
            return subprocess.CompletedProcess(command, exit_code, b"synthetic recognizer stdout", diagnostic)
        with patch("agent_speech_processor.whisper.shutil.which", return_value=str(self.engine)), \
                patch("agent_speech_processor.whisper.recipe", return_value=(self.spec, getattr(self, "snapshot", b"synthetic source snapshot"))), \
                patch("agent_speech_processor.whisper.run", side_effect=recognize), \
                contextlib.redirect_stdout(io.StringIO()):
            return process(self.args)

    def receipt(self):
        return json.loads((self.folder / ".processing.json").read_text())


class EditorialPipeline(DraftFixture, unittest.TestCase):
    def test_resume_without_wavs_preserves_edited_words_and_html_notes(self):
        self.run_draft()
        self.assertFalse(list(self.folder.rglob("*.wav")))
        draft = (self.folder / "transcript-draft.md").read_bytes()
        edited = self.folder / "transcript-edited.md"
        content = edited.read_text() + "\n<!-- Interpretation: preserve the explicit denial. -->\n"
        edited.write_text(content.replace("No, only", "No; only"))
        annotated = edited.read_bytes()
        result = self.run_draft()
        self.assertTrue(result["editorial"]["preserved_existing"])
        self.assertEqual(self.calls, 1)
        self.assertEqual(edited.read_bytes(), annotated)
        self.assertEqual((self.folder / "transcript-draft.md").read_bytes(), draft)
        args = parser().parse_args(["verify", "--output", str(self.folder)])
        with patch("agent_speech_processor.cli.workspace", return_value={}):
            verified, code = dispatch(args)
        self.assertEqual(code, 0)
        self.assertTrue(verified["verified"])
        imported = import_draft(self.folder, self.source, checksum(self.source),
                                digest(self.receipt()["specification"]["settings"]))
        self.assertEqual(imported["chunks"], 1)

    def test_missing_raw_json_is_an_error_not_a_recognition_rerun(self):
        self.run_draft()
        next((self.folder / "chunks").glob("*.json")).unlink()
        with self.assertRaisesRegex(ValueError, "missing or modified"):
            self.run_draft()
        self.assertEqual(self.calls, 1)

    def test_interrupted_publication_reuses_recognition_without_a_wav(self):
        def interrupt(path, data, **options):
            atomic(path, data, **options)
            if Path(path).parent.name == "chunks" and Path(path).suffix == ".json":
                raise KeyboardInterrupt()
        with patch("agent_speech_processor.job.atomic", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                self.run_draft()
        self.assertEqual(self.calls, 1)
        self.assertFalse(list(self.folder.rglob("*.wav")))
        self.run_draft()
        self.assertEqual(self.calls, 1)
        self.assertTrue((self.folder / "transcript-edited.md").is_file())
        self.assertFalse(list(self.folder.glob(".stage-*")))

    def test_low_disk_between_chunks_retains_completed_work(self):
        with wave.open(str(self.source), "wb") as sound:
            sound.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            sound.writeframes(b"\0\0" * 16000 * 30)
        self.args.chunk_seconds = 20
        from agent_speech_processor.audio import check_space
        checks = 0
        def limited(*args):
            nonlocal checks
            checks += 1
            if checks == 3:
                raise RuntimeError("Not enough free disk for the next draft chunk and 2 GiB reserve")
            return check_space(*args)
        with patch("agent_speech_processor.whisper.check_space", side_effect=limited):
            with self.assertRaisesRegex(RuntimeError, "2 GiB reserve"):
                self.run_draft()
        self.assertEqual(self.calls, 1)
        self.assertEqual(len(list((self.folder / "chunks").glob("*.json"))), 1)
        self.run_draft()
        self.assertEqual(self.calls, 2)

    def test_missing_user_edited_file_is_not_silently_recreated(self):
        self.run_draft()
        (self.folder / "transcript-edited.md").unlink()
        with self.assertRaisesRegex(ValueError, "restore the user-owned"):
            self.run_draft()
        self.assertEqual(self.calls, 1)

    def test_edited_directory_is_not_misreported_as_a_preserved_transcript(self):
        self.run_draft()
        edited = self.folder / "transcript-edited.md"
        edited.unlink()
        edited.mkdir()
        with self.assertRaisesRegex(ValueError, "regular file"):
            self.run_draft()
        self.assertTrue(edited.is_dir())
        self.assertEqual(self.calls, 1)

    def test_low_disk_stops_before_recognition_and_resumes(self):
        with patch("agent_speech_processor.audio.shutil.disk_usage", return_value=shutil._ntuple_diskusage(100, 99, 1)):
            with self.assertRaisesRegex(RuntimeError, "2 GiB reserve"):
                self.run_draft()
        self.assertEqual(self.calls, 0)
        self.assertFalse(self.folder.exists())
        self.run_draft()
        self.assertEqual(self.calls, 1)

    def test_changed_runtime_requires_a_new_variant(self):
        with wave.open(str(self.source), "wb") as sound:
            sound.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
            sound.writeframes(b"\0\0" * 16000 * 30)
        self.args.chunk_seconds, self.args.limit_chunks = 20, 1
        self.run_draft()
        before = (self.folder / ".processing.json").read_bytes()
        self.spec["environment"]["python"] = "another version"
        with self.assertRaisesRegex(ValueError, "Inputs/settings differ"):
            self.run_draft()
        self.assertEqual((self.folder / ".processing.json").read_bytes(), before)
        self.assertEqual(self.calls, 1)

    def test_moving_archive_as_a_unit_keeps_content_identity(self):
        self.run_draft()
        with tempfile.TemporaryDirectory() as other:
            moved = Path(other) / "archive"
            shutil.copytree(self.root, moved)
            self.root, self.source, self.folder = moved, moved / "source.wav", moved / "draft"
            self.args.source, self.args.output = self.source, self.folder
            self.run_draft()
            state = self.receipt()
            self.assertEqual(state["specification"]["sources"], ["../source.wav"])
            Job(self.folder, state).verify_inputs_unchanged()
            import_draft(self.folder, self.source, checksum(self.source), digest(state["specification"]["settings"]))
        self.assertEqual(self.calls, 1)

    def test_pcm_recipe_recreates_exact_recognition_input(self):
        self.run_draft()
        item = next(step["details"] for step in self.receipt()["steps"].values() if "pcm" in step.get("details", {}))
        wav = self.root / "recreated.wav"
        command = [str(self.source) if x == "{source}" else str(wav) if x == "{wav}" else x for x in item["extract_argv"]]
        run(command)
        self.assertEqual(pcm_identity(wav), item["pcm"])

    def prune(self, apply=False):
        from agent_speech_processor.retention import process as prune
        return prune(argparse.Namespace(output=self.folder, fetch=False, apply=apply))

    def test_pruning_preserves_receipt_text_and_read_only_intake(self):
        self.args.keep_audio = True
        self.run_draft()
        original = (self.folder / ".processing.json").read_bytes()
        text = self.folder / "transcript-edited.md"
        text.write_text(text.read_text() + "\n<!-- Human interpretation survives cache cleanup. -->\n")
        note = text.read_bytes()
        plan = self.prune()
        self.assertEqual(len(plan["files"]), 1)
        self.assertTrue(list(self.folder.rglob("*.wav")))
        result = self.prune(True)
        self.assertEqual(result["removed_files"], 1)
        self.assertFalse(list(self.folder.rglob("*.wav")))
        self.assertEqual((self.folder / ".processing.json").read_bytes(), original)
        self.assertEqual((self.folder / ".processing.before-audio-prune.json").read_bytes(), original)
        self.assertEqual(text.read_bytes(), note)
        self.run_draft()
        self.assertEqual(self.calls, 1)
        self.assertFalse(list(self.folder.rglob("*.wav")))
        import_draft(self.folder, self.source, checksum(self.source),
                     digest(self.receipt()["specification"]["settings"]))

    def test_changed_recreation_stops_without_deleting_anything(self):
        self.args.keep_audio = True
        self.run_draft()
        from agent_speech_processor.retention import extraction_command
        def changed(source, start, end, indices, wav, adapter):
            return extraction_command(source, start, end / 2, indices, wav, adapter)
        with patch("agent_speech_processor.retention.extraction_command", side_effect=changed):
            with self.assertRaisesRegex(ValueError, "Recreated WAV differs"):
                self.prune(True)
        self.assertEqual(len(list(self.folder.rglob("*.wav"))), 1)
        self.assertFalse((self.folder / "audio-retention.json").exists())

    def test_retention_exception_cannot_hide_missing_raw_json(self):
        self.args.keep_audio = True
        self.run_draft()
        self.prune(True)
        path = self.folder / "audio-retention.json"
        record = json.loads(path.read_text())
        raw = next((self.folder / "chunks").glob("*.json"))
        value = checksum(raw)
        record["disposable"][str(raw.relative_to(self.folder))] = dict(sha256=value, recreated_sha256=value)
        path.write_text(json.dumps(record))
        raw.unlink()
        with self.assertRaisesRegex(ValueError, "Retention exception"):
            recorded_job(self.folder, self.receipt()).required_output_paths()

    def test_interrupted_cleanup_keeps_verifiable_retry_state(self):
        self.args.keep_audio = True
        self.run_draft()
        unlink = Path.unlink
        def interrupted(path, *args, **kwargs):
            unlink(path, *args, **kwargs)
            if path.parent.name == "chunks" and path.suffix == ".wav":
                raise KeyboardInterrupt()
        with patch.object(Path, "unlink", interrupted):
            with self.assertRaises(KeyboardInterrupt):
                self.prune(True)
        self.assertFalse(list(self.folder.rglob("*.wav")))
        state = self.receipt()
        for step in state["steps"].values():
            for name, value in step["outputs"].items():
                recorded_job(self.folder, state).verify_output(name, value)
        self.assertEqual(self.prune(True)["files"], [])


class EditorialEvidence(unittest.TestCase):
    def item(self, start, end, text):
        return dict(start=start, end=end, text=text, note="")

    def test_exact_shared_context_removed_but_unique_continuation_survives(self):
        left = [self.item(115, 122, "No, only in the trial.")]
        right = [self.item(115, 125, "No, only in the trial. The rest stayed unchanged.")]
        result, count = trim_boundary(left, right, 115, 125)
        self.assertEqual(count, 5)
        self.assertEqual(result[0]["text"], "The rest stayed unchanged.")

    def test_denial_disagreement_and_later_repetition_are_not_discarded(self):
        left = [self.item(115, 120, "It was used throughout the organization.")]
        right = [self.item(116, 122, "No, it was used only in the trial.")]
        result, count = trim_boundary(left, right, 115, 125)
        self.assertEqual(result, right)
        self.assertEqual(count, 0)
        later = [self.item(121, 125, left[0]["text"])]
        self.assertEqual(trim_boundary(left, later, 115, 125)[1], 0)

    def test_final_rounding_locators_are_playable_and_raw_json_is_unchanged(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = json.dumps({"transcription": [{"offsets": {"from": 0, "to": 1020}, "text": "Synthetic final words."}]})
            (root / "chunk.json").write_text(raw)
            chunks = [dict(start_seconds=0, end_seconds=1.010998, json="chunk.json")]
            located = located_chunks(root, chunks, 1.010998)[0]["items"][0]
            self.assertEqual(located["end"], 1.010)
            source_range(located["start"], located["end"], 0, 1.010998)
            self.assertIn("rounding", located["note"])
            self.assertEqual((root / "chunk.json").read_text(), raw)

    def test_defaults_are_bounded_and_recognizer_context_override_is_explicit(self):
        args = parser().parse_args(["draft", "--source", "source", "--output", "out", "--model", "model"])
        self.assertEqual((args.chunk_seconds, args.max_context, args.keep_audio), (120, 0, False))
        args = parser().parse_args(["draft", "--source", "source", "--output", "out", "--model", "model", "--max-context", "default"])
        self.assertIsNone(args.max_context)

    def test_gpu_startup_timings_do_not_change_tool_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            engine = Path(directory) / "whisper-cli"
            engine.write_bytes(b"synthetic executable")
            def response(seconds):
                return subprocess.CompletedProcess([], 0, b"", (
                    "load_backend: loaded Metal backend from /synthetic/libmetal.so\n"
                    f"ggml_metal_library_compile_all: compiled library in {seconds} sec\n"
                    f"usage: {engine} [options]\n  -mc N text context\n").encode())
            with patch("agent_speech_processor.provenance.run", side_effect=[response('0.070'), response('0.001')]), \
                    patch("agent_speech_processor.provenance.library_identity", return_value={"libraries": {"libmetal.so": "same hash"}, "unresolved": []}) as libraries:
                first = tool_identity("whisper-cli", engine, ["--help"])
                second = tool_identity("whisper-cli", engine, ["--help"])
            self.assertEqual(first, second)
            self.assertEqual(libraries.call_args.args[1], ["/synthetic/libmetal.so"])
            self.assertNotIn("compiled", first["version_or_help"])


if __name__ == "__main__":
    unittest.main()

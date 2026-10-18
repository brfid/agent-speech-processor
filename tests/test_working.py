"""Canonical user text stays editable, portable, and independent of machine state."""
import copy
import json
import os
from pathlib import Path
import shutil
import tempfile
import unittest
from unittest.mock import patch

from agent_speech_processor.drafts import draft_completion
from agent_speech_processor.job import Job
from agent_speech_processor.transcripts import initialize_edited
from agent_speech_processor import working
from test_editorial_pipeline import DraftFixture


def register(folder, manifest, transcript, source):
    folder, manifest, transcript, source = map(lambda p: Path(p).resolve(), (folder, manifest, transcript, source))
    item = {"source": os.path.relpath(source, manifest.parent), "draft": os.path.relpath(folder, manifest.parent),
            "source_sha256": "a" * 64, "recipe_sha256": "b" * 64, "audio_streams": [0],
            "working_transcript": os.path.relpath(transcript, manifest.parent)}
    manifest.write_text(json.dumps({"schema": 1, "reading_layout": 1, "recordings": [item]}))
    (folder / working.SIDECAR).write_text(json.dumps({"schema": 1, "manifest": os.path.relpath(manifest, folder)}))
    return item


class WorkingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.archive = self.root / "archive"
        self.folder = self.archive / "processed/session/recording"
        self.reading = self.archive / "reading/Supplied description"
        self.folder.mkdir(parents=True)
        self.reading.mkdir(parents=True)
        self.source = self.archive / "originals/recording.wav"  # Deliberately absent: reading needs no audio.
        self.manifest = self.reading / ".session.json"
        self.transcript = self.reading / "Working transcript.md"
        self.transcript.write_text("# User-owned working text\n[00:01–00:02] Revised words.\n<!-- Keep this interpretation. -->\n")
        self.item = register(self.folder, self.manifest, self.transcript, self.source)
        self.edited = self.folder / "transcript-edited.md"
        self.edited.write_text("Historical editable seed preserved as evidence.")
        (self.folder / "transcript-draft.md").write_text("Protected machine baseline")
        self.state = {"schema": 2, "specification": {"kind": "audio", "sources": [os.path.relpath(self.source, self.folder)],
                      "settings": {"chunk_seconds": 120, "overlap_seconds": 5, "adapter_version": 4, "recipe_version": 2}},
                      "steps": {"media-probe": {"status": "done", "details": {"duration": 10}},
                                "chunk": {"status": "done", "details": {"json": "chunk.json", "start_seconds": 0, "end_seconds": 10},
                                          "outputs": {"chunk.json": "unused"}},
                                "combined-draft": {"status": "done"}},
                      "editorial": {"status": "created", "initial_sha256": "historical-seed-hash"}}
        self.current = Job(self.folder, self.state)

    def test_changed_canonical_text_is_preserved_before_seed_or_receipt_checks(self):
        historical, state = self.edited.read_bytes(), copy.deepcopy(self.state)
        for addition in ("First requested correction.", "Second requested correction."):
            self.transcript.write_text(self.transcript.read_text() + addition + "\n")
            expected = self.transcript.read_bytes()
            with patch.object(self.current, "save", side_effect=AssertionError("Must not rewrite historical state")), \
                 patch("agent_speech_processor.transcripts.checksum", side_effect=AssertionError("Must not hash baseline or seed")), \
                 patch("agent_speech_processor.transcripts.edited_text", side_effect=AssertionError("Must not regenerate")):
                result = initialize_edited(self.current, self.source, 10, [0], [])
            self.assertEqual(result["path"], str(self.transcript))
            self.assertTrue(result["registered_working_transcript"])
            self.assertTrue(result["preserved_existing"])
            self.assertEqual(self.transcript.read_bytes(), expected)
        self.assertEqual(self.edited.read_bytes(), historical)
        self.assertEqual(self.state, state)
        self.assertTrue(draft_completion(self.current)["complete"])

    def test_missing_registered_text_never_falls_back_or_reseeds(self):
        self.transcript.unlink()
        with self.assertRaisesRegex(ValueError, "restore the user-owned"):
            working.resolve(self.folder)
        with patch("agent_speech_processor.transcripts.edited_text", side_effect=AssertionError("Must not reseed")):
            with self.assertRaisesRegex(ValueError, "restore the user-owned"):
                initialize_edited(self.current, self.source, 10, [0], [])
        report = draft_completion(self.current)
        self.assertTrue(report["machine_draft_complete"])
        self.assertTrue(report["edited_transcript_present"])
        self.assertFalse(report["working_transcript_present"])
        self.assertFalse(report["complete"])
        self.assertIn("restore", report["working_transcript_error"])
        self.assertFalse(self.transcript.exists())

    def test_explicit_mapping_requires_agreement_with_registered_authority(self):
        result = working.from_manifest(self.manifest, self.item, self.folder, expected_source=self.source)
        self.assertEqual(result["transcript"], self.transcript)
        alternate = self.reading / "Alternate.md"
        alternate.write_text("Other working copy must not be selected silently")
        manifest = self.reading / "other.json"
        item = {**self.item, "working_transcript": alternate.name}
        manifest.write_text(json.dumps({"schema": 1, "reading_layout": 1, "recordings": [item]}))
        with self.assertRaisesRegex(ValueError, "conflicts"):
            working.from_manifest(manifest, item, self.folder)
        with self.assertRaisesRegex(ValueError, "differs"):
            working.from_manifest(self.manifest, item, self.folder)
        with self.assertRaisesRegex(ValueError, "another original"):
            working.resolve(self.folder, expected_source=self.archive / "other.wav")

    def test_duplicate_job_mapping_and_wrong_job_are_recovery_errors(self):
        data = json.loads(self.manifest.read_text())
        for rows in ([self.item, self.item], [{**self.item, "draft": "elsewhere"}]):
            data["recordings"] = rows
            self.manifest.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, "exactly one"):
                working.resolve(self.folder)
            report = draft_completion(self.current)
            self.assertTrue(report["machine_draft_complete"])
            self.assertFalse(report["complete"])
            self.assertIn("exactly one", report["working_transcript_error"])

    def test_distinct_recordings_cannot_share_working_text_or_use_processing_text(self):
        other_job = self.archive / "processed/another-job"
        other_job.mkdir()
        other = {**self.item, "draft": os.path.relpath(other_job, self.reading), "source": "../../originals/other.wav"}
        data = json.loads(self.manifest.read_text())
        data["recordings"].append(other)
        self.manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "share one"):
            working.resolve(self.folder)
        data["recordings"] = [self.item]
        for target in (other_job / "transcript-draft.md", self.edited, self.reading / "nested/working.md"):
            with self.subTest(target=target):
                data["recordings"][0]["working_transcript"] = os.path.relpath(target, self.reading)
                self.manifest.write_text(json.dumps(data))
                with self.assertRaisesRegex(ValueError, "outside processing jobs"):
                    working.resolve(self.folder, require=False)

    def test_symlink_controls_text_and_intermediate_locators_are_rejected(self):
        for path in (self.folder / working.SIDECAR, self.manifest, self.transcript):
            with self.subTest(path=path):
                target = path.with_name(path.name + ".retained")
                path.rename(target)
                path.symlink_to(target)
                try:
                    with self.assertRaisesRegex(ValueError, "symlink"):
                        working.resolve(self.folder, require=False)
                finally:
                    path.unlink()
                    target.rename(path)
        link = self.reading / "linked"
        link.symlink_to(self.reading, target_is_directory=True)
        data = json.loads(self.manifest.read_text())
        data["recordings"][0]["working_transcript"] = "linked/Working transcript.md"
        self.manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "symlink"):
            working.resolve(self.folder, require=False)
        data["recordings"][0]["working_transcript"] = "linked/../Working transcript.md"
        self.manifest.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, "symlink"):
            working.resolve(self.folder)

    def test_unsafe_or_nontext_locators_are_rejected_before_fetching_payload(self):
        data = json.loads(self.manifest.read_text())
        for locator in (str(self.transcript), "", self.item["source"], self.item["draft"] + "/transcript-draft.md"):
            with self.subTest(locator=locator):
                data["recordings"][0]["working_transcript"] = locator
                self.manifest.write_text(json.dumps(data))
                with patch("agent_speech_processor.working.materialize") as fetch:
                    with self.assertRaises(ValueError):
                        working.resolve(self.folder, fetch=True)
                paths = [p for call in fetch.call_args_list for p in call.args[0]]
                self.assertNotIn(self.source, paths)
                self.assertNotIn(self.folder / "transcript-draft.md", paths)

    def test_entire_archive_relocation_preserves_relative_registration_and_edits(self):
        original = self.transcript.read_bytes()
        moved = self.root / "relocated"
        shutil.move(self.archive, moved)
        folder = moved / "processed/session/recording"
        transcript = moved / "reading/Supplied description/Working transcript.md"
        result = working.resolve(folder, expected_source=moved / "originals/recording.wav")
        self.assertEqual(result["transcript"], transcript)
        self.assertEqual(result["transcript"].read_bytes(), original)
        current = Job(folder, self.state)
        self.assertTrue(draft_completion(current)["complete"])

    def test_fetch_selects_only_exact_controls_and_required_canonical_text(self):
        with patch("agent_speech_processor.working.materialize") as fetch:
            working.resolve(self.folder, fetch=True)
        paths = [p for call in fetch.call_args_list for p in call.args[0]]
        self.assertEqual(paths, [self.folder / working.SIDECAR, self.manifest, self.transcript])
        with patch("agent_speech_processor.working.materialize") as fetch:
            result = working.resolve(self.folder, fetch=True, require=False)
        self.assertTrue(result["available"])
        paths = [p for call in fetch.call_args_list for p in call.args[0]]
        self.assertEqual(paths, [self.folder / working.SIDECAR, self.manifest])
        actual_read = Path.read_text
        def controls_only(path, *args, **kwargs):
            if path == self.transcript:
                raise AssertionError("Completion must not read user text")
            return actual_read(path, *args, **kwargs)
        with patch("agent_speech_processor.working.materialize", side_effect=AssertionError("Completion must not fetch")), \
             patch.object(Path, "read_text", controls_only):
            report = draft_completion(self.current)
        self.assertTrue(report["working_transcript_present"])
        self.assertFalse(self.source.exists())
        self.assertFalse((self.folder / ".processing.json").exists())

    def test_current_unpromoted_draft_preserves_its_editorial_seed(self):
        (self.folder / working.SIDECAR).unlink()
        self.assertIsNone(working.resolve(self.folder))
        result = initialize_edited(self.current, self.source, 10, [0], [])
        self.assertEqual(result["path"], str(self.edited))
        report = draft_completion(self.current)
        self.assertFalse(report["working_transcript_registered"])
        self.assertTrue(report["complete"])
        self.edited.unlink()
        with self.assertRaisesRegex(ValueError, "restore the user-owned"):
            initialize_edited(self.current, self.source, 10, [0], [])


class CanonicalResume(DraftFixture, unittest.TestCase):
    def test_completed_draft_resume_uses_canonical_text_without_recognition_or_seed_rewrite(self):
        self.run_draft()
        reading = self.root.resolve() / "reading"
        reading.mkdir()
        transcript = reading / "Working transcript.md"
        transcript.write_text("Canonical human correction.\n<!-- Human interpretation remains. -->\n")
        manifest = reading / ".session.json"
        register(self.folder, manifest, transcript, self.source)
        old_editorial = copy.deepcopy(self.receipt()["editorial"])
        old_seed = (self.folder / "transcript-edited.md").read_bytes()
        result = self.run_draft()
        self.assertEqual(self.calls, 1)
        self.assertEqual(result["editorial"]["path"], str(transcript))
        self.assertTrue(result["draft_completion"]["working_transcript_registered"])
        self.assertTrue(result["draft_completion"]["complete"])
        self.assertEqual(self.receipt()["editorial"], old_editorial)
        self.assertEqual((self.folder / "transcript-edited.md").read_bytes(), old_seed)
        self.assertIn("Human interpretation", transcript.read_text())

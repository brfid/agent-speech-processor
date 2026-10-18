"""Synthetic evidence, time and publication tests; no model weights or interviews."""
import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import wave

from agent_speech_processor.records import atomic, checksum, json_bytes
from agent_speech_processor.job import job
from agent_speech_processor.processes import run
from agent_speech_processor.records import digest
from agent_speech_processor.drafts import import_draft
from agent_speech_processor.experimental.audio import prepare_window, windows
from agent_speech_processor.audio import wav_info
from agent_speech_processor.experimental.render import publish
from agent_speech_processor.experimental.review import validate_response, moss_segments
from agent_speech_processor.experimental.pipeline import check_qualification, process
from agent_speech_processor.session import load_manifest
from agent_speech_processor.experimental.metrics import coverage, errors, speaker_errors, subtract


class ReviewEvidence(unittest.TestCase):
    def setUp(self):
        self.span = dict(index=0, start=0, end=2, core_start=0, core_end=2)
        self.audio = [dict(sha256="a" * 64, view="mix", source_start=0, source_end=2,
                           activity={"voiced": [[0, 2]]})]
        self.baseline = [dict(start=0, end=2, text="one two three four", speaker="unknown")]
        self.record = {"segments": [dict(start=0, end=2, text="one two three four", speaker="S01",
                                          uncertain=False, reason="The words are audible")],
                       "unresolved": [], "silence": False}

    def response(self):
        return {"ok": True, "truncated": False, "text": json.dumps(self.record),
                "audio_consumed": [dict(self.audio[0], duration=2, samples=32000, sample_rate=16000)]}

    def validate(self, response=None):
        return validate_response(response or self.response(), self.span, self.baseline,
                                 [dict(start=0, end=2, text="one two three four", speaker="S01")], [], self.audio)

    def test_unchanged_words_still_require_audio_evidence(self):
        self.assertEqual(len(self.validate()["segments"]), 1)
        response = self.response()
        response["audio_consumed"] = []
        with self.assertRaisesRegex(ValueError, "every required audio"):
            self.validate(response)

    def test_missing_separate_track_is_rejected(self):
        response = self.response()
        self.audio.append(dict(self.audio[0], view="0:a:1"))
        with self.assertRaises(ValueError):
            self.validate(response)

    def test_changed_audio_or_truncated_input_is_rejected(self):
        for change in ({"sha256": "b" * 64}, {"duration": 1.0}, {"sample_rate": 8000}):
            response = self.response()
            response["audio_consumed"][0].update(change)
            with self.assertRaises(ValueError):
                self.validate(response)

    def test_nonfinite_reversed_zero_or_out_of_core_times_are_rejected(self):
        for start, end in ((float("nan"), 1), (1, float("inf")), (1, 0), (1, 1), (-1, 2), (0, 3)):
            self.record["segments"][0].update(start=start, end=end)
            with self.assertRaises(ValueError):
                self.validate()

    def test_documented_names_and_roles_do_not_identify_a_voice(self):
        for speaker in ("Interviewer", "Dr Example", "S99"):
            self.record["segments"][0]["speaker"] = speaker
            with self.assertRaises(ValueError):
                self.validate()

    def test_malformed_json_and_missing_reason_are_rejected(self):
        response = self.response()
        response["text"] = "{'segments': []}"
        with self.assertRaises(ValueError):
            self.validate(response)
        self.record["segments"][0]["reason"] = ""
        with self.assertRaises(ValueError):
            self.validate()

    def test_large_unsupported_rewrite_is_rejected(self):
        self.record["segments"][0]["text"] = "a completely different invented historical account"
        with self.assertRaisesRegex(ValueError, "rewrite"):
            self.validate()

    def test_possible_missing_speech_becomes_an_explicit_uncertainty(self):
        self.record["segments"][0]["end"] = 1
        checked = self.validate()
        self.assertEqual(checked["unresolved"][0]["start"], 1)
        self.assertEqual(checked["unresolved"][0]["end"], 2)

    def test_silence_cannot_discard_draft_words(self):
        self.record.update(segments=[], silence=True)
        with self.assertRaisesRegex(ValueError, "removed draft speech"):
            self.validate()

    def test_token_limit_is_never_treated_as_complete(self):
        response = self.response()
        response["truncated"] = True
        with self.assertRaises(ValueError):
            self.validate(response)

    def test_moss_keeps_overlapping_anonymous_turns(self):
        response = dict(ok=True, segments=[dict(start=0, end=1.5, text="[S01] one", speaker_id="S01"),
                                           dict(start=1, end=2, text="[S02] two", speaker_id="S02")])
        result = moss_segments(response, 2)
        self.assertEqual([s["speaker"] for s in result], ["S01", "S02"])
        self.assertEqual(result[0]["text"], "one")


class Coverage(unittest.TestCase):
    def test_context_does_not_double_count_core_coverage(self):
        spans = list(windows(41.5))
        self.assertEqual([(s["core_start"], s["core_end"]) for s in spans], [(0, 20), (20, 40), (40, 41.5)])
        full = coverage(41.5, [dict(span=s, accepted=True) for s in spans])
        self.assertTrue(full["complete"])
        self.assertEqual(full["reviewed_seconds"], 41.5)
        partial = coverage(41.5, [dict(span=s, accepted=i != 1) for i, s in enumerate(spans)])
        self.assertEqual(partial["unreviewed_intervals"], [[20, 40]])

    def test_leading_trailing_and_internal_gaps_are_preserved(self):
        self.assertEqual(subtract([(0, 10)], [(1, 3), (5, 8)]), [[0, 1], [3, 5], [8, 10]])

    def test_backend_limit_is_enforced(self):
        for core, context in ((30, 1), (0, 1), (10, -1)):
            with self.assertRaises(ValueError):
                list(windows(60, core, context))

    def test_word_and_speaker_metrics_detect_loss_and_merged_speakers(self):
        self.assertEqual(errors("one two three", "one three")["errors"], 1)
        reference = [dict(start=0, text="one two", speaker="a"), dict(start=1, text="three four", speaker="b")]
        permuted = [dict(reference[0], speaker="b"), dict(reference[1], speaker="a")]
        self.assertEqual(speaker_errors(reference, permuted)["errors"], 0)
        merged = [dict(s, speaker="unknown") for s in reference]
        self.assertGreater(speaker_errors(reference, merged)["errors"], 0)


class Publication(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_existing_human_transcript_survives(self):
        target = self.root / "transcript-reviewed.md"
        target.write_text("Human wording")
        with self.assertRaises(FileExistsError):
            publish(self.root, "Model wording", "recipe", "source")
        self.assertEqual(target.read_text(), "Human wording")

    def test_interrupted_publication_resumes_without_overwriting(self):
        def interrupt(path, data, **kwargs):
            atomic(path, data, **kwargs)
            if Path(path).name == ".review-publication.json":
                raise KeyboardInterrupt()
        with patch("agent_speech_processor.experimental.render.atomic", side_effect=interrupt):
            with self.assertRaises(KeyboardInterrupt):
                publish(self.root, "Transcript", "recipe", "source")
        publish(self.root, "Transcript", "recipe", "source")
        publish(self.root, "Transcript", "recipe", "source")
        target = self.root / "transcript-reviewed.md"
        target.write_text("Later correction")
        with self.assertRaises(ValueError):
            publish(self.root, "Transcript", "recipe", "source")
        self.assertEqual(target.read_text(), "Later correction")

    def test_unqualified_or_changed_recipe_cannot_publish(self):
        path = self.root / "qualification.json"
        path.write_text(json.dumps(dict(passed=True, cases=3, recipe_sha256="wrong", reference_sha256="test")))
        with self.assertRaisesRegex(ValueError, "passing reference qualification"):
            check_qualification(path, {"recipe": 1})


class RealMedia(unittest.TestCase):
    def test_separate_track_offset_and_window_start_are_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "offset.mov"
            run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=2",
                 "-itsoffset", "0.6", "-f", "lavfi", "-i", "sine=frequency=880:duration=1.4",
                 "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le", source])
            span = dict(index=0, start=0.3, end=1.3, core_start=0.3, core_end=1.3)
            views = prepare_window(source, span, [0, 1], root)
            self.assertEqual([v["view"] for v in views], ["mix", "0:a:0", "0:a:1"])
            raw, duration = wav_info(root / "stream-1.wav")
            self.assertEqual(duration, 1)
            self.assertEqual(set(raw[:int(0.25 * 16000) * 2]), {0})
            self.assertNotEqual(set(raw[int(0.5 * 16000) * 2:]), {0})


def draft_fixture(root):
    source = root / "source.wav"
    with wave.open(str(source), "wb") as output:
        output.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        output.writeframes(b"\0\0" * 16000)
    folder = root / "draft"
    settings = dict(chunk_seconds=120, overlap_seconds=5, audio_stream="0:a:0", adapter_version=4, recipe_version=2)
    with job(folder, "audio", [source], settings, False, relative_sources=True) as current:
        def media(tmp):
            (tmp / "media.json").write_text("{}")
            return {"media.json": tmp / "media.json"}, {"duration": 1}
        current.step("media-probe", media)
        def chunk(tmp):
            (tmp / "draft.json").write_bytes(json_bytes({"transcription": [
                {"offsets": {"from": 0, "to": 1000}, "text": "one two"}]}))
            return {"chunks/draft.json": tmp / "draft.json"}, dict(start_seconds=0, end_seconds=1, json="chunks/draft.json")
        current.step("chunk", chunk)
        def combined(tmp):
            (tmp / "draft.md").write_text("Machine draft")
            return {"transcript-draft.md": tmp / "draft.md"}, {"audio_checked": False}
        current.step("combined-draft", combined)
    return source, folder, settings


class ImportAndPilot(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source, self.folder, self.settings = draft_fixture(self.root)

    def test_current_import_is_read_only_and_rejects_changed_recipe_or_output(self):
        before = checksum(self.folder / ".processing.json")
        imported = import_draft(self.folder, self.source, checksum(self.source), digest(self.settings))
        self.assertEqual(imported["chunks"], 1)
        self.assertEqual(before, checksum(self.folder / ".processing.json"))
        with self.assertRaises(ValueError):
            import_draft(self.folder, self.source, checksum(self.source), "wrong")
        (self.folder / "transcript-draft.md").write_text("Edited draft")
        with self.assertRaisesRegex(ValueError, "Modified draft"):
            import_draft(self.folder, self.source, checksum(self.source), digest(self.settings))

    def test_pilot_resumes_and_never_publishes_a_reviewed_transcript(self):
        from argparse import Namespace
        manifest = self.root / "session.json"
        manifest.write_bytes(json_bytes({"schema": 1, "recordings": [{
            "source": str(self.source), "draft": str(self.folder), "source_sha256": checksum(self.source),
            "draft_recipe_sha256": digest(self.settings), "audio_streams": [0]}]}))
        args = Namespace(session=manifest, fetch=False, variant="test", pilot=True, qualification=None,
                         limit_windows=None, retry_failed=False)
        recipe = {"core_seconds": 20, "context_seconds": 2,
                  "reviewer": dict(model_id="synthetic", revision="fixture"),
                  "diarizer": dict(model_id="synthetic", revision="fixture")}
        def fake(tmp, source, span, *unused):
            result = dict(span=span, accepted=True, review={"segments": [], "unresolved": [], "silence": True})
            (tmp / "result.json").write_bytes(json_bytes(result))
            return result
        before = checksum(self.folder / ".processing.json")
        with patch("agent_speech_processor.experimental.pipeline.window_result", side_effect=fake) as call:
            first = process(args, recipe)
            second = process(args, recipe)
        self.assertEqual(call.call_count, 1)
        self.assertTrue(first["recordings"][0]["coverage"]["complete"])
        self.assertFalse(second["complete"])
        self.assertFalse((self.folder / "transcript-reviewed.md").exists())
        self.assertEqual(before, checksum(self.folder / ".processing.json"))

"""Playback extraction boundaries, using only disposable synthetic recordings."""
import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from agent_speech_processor.processes import run
from agent_speech_processor.clips import prepare_clip
from agent_speech_processor.audio import probe, source_duration, wav_info


class PlaybackAudio(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.source = self.root / "source.mov"
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-itsoffset", "1.5", "-f", "lavfi", "-i", "sine=frequency=880:duration=1",
             "-map", "0:a", "-map", "1:a", "-c:a", "pcm_s16le", self.source])
        self.original = self.source.read_bytes()

    def clip(self, start, end, indices=(1,)):
        folder = self.root / f"clip-{start}-{end}"
        folder.mkdir()
        path = prepare_clip(self.source, dict(start=start, end=end), list(indices), folder)
        self.assertEqual(path, folder / "mix.wav")
        raw, duration = wav_info(path)
        self.assertAlmostEqual(duration, end - start, delta=1 / 16000)
        self.assertEqual(self.source.read_bytes(), self.original)
        return raw

    def test_late_and_ended_tracks_are_silence_on_the_same_timeline(self):
        # These windows contain no packets for stream 1, but still need a clip.
        self.assertEqual(set(self.clip(0, 0.5)), {0})
        self.assertEqual(set(self.clip(2.6, 3)), {0})
        raw = self.clip(1, 2)
        self.assertEqual(set(raw[:int(0.45 * 16000) * 2]), {0})
        self.assertNotEqual(set(raw[int(0.6 * 16000) * 2:]), {0})
        raw = self.clip(2, 3)
        self.assertNotEqual(set(raw[:int(0.4 * 16000) * 2]), {0})
        self.assertEqual(set(raw[int(0.6 * 16000) * 2:]), {0})

    def test_mixed_streams_need_no_vad_or_evidence_hashing(self):
        with patch("agent_speech_processor.experimental.audio.activity", side_effect=AssertionError("No VAD")), \
                patch("agent_speech_processor.experimental.audio.checksum", side_effect=AssertionError("No hashing")), \
                patch.dict("sys.modules", {"webrtcvad": None}), \
                patch("agent_speech_processor.clips.run", wraps=run) as invoked:
            self.assertNotEqual(set(self.clip(0, 3, [0, 1])), {0})
        self.assertEqual(len(invoked.call_args_list), 3)
        self.assertTrue(all(call.kwargs["timeout"] == 60 for call in invoked.call_args_list))

    def test_existing_files_are_not_overwritten(self):
        folder = self.root / "existing"
        folder.mkdir()
        existing = folder / "stream-0.wav"
        existing.write_bytes(b"existing user file")
        with self.assertRaisesRegex(ValueError, "already exists"):
            prepare_clip(self.source, dict(start=0, end=1), [0], folder)
        self.assertEqual(existing.read_bytes(), b"existing user file")
        self.assertEqual(self.source.read_bytes(), self.original)

    def test_nonzero_container_start_is_relative_to_recording(self):
        shifted = self.root / "shifted.mov"
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-af", "asetpts=PTS+5/TB", "-c:a", "pcm_s16le", shifted])
        info = probe(shifted, timeout=30)
        self.assertEqual(float(info["probe"]["format"]["start_time"]), 5)
        self.assertEqual(info["duration"], 3)
        self.assertEqual(source_duration(info), 3)
        for start, end in ((0, 0.5), (2.5, 3)):
            folder = self.root / f"shifted-{start}"
            folder.mkdir()
            path = prepare_clip(shifted, dict(start=start, end=end), [0], folder)
            raw, duration = wav_info(path)
            self.assertEqual(duration, 0.5)
            self.assertNotEqual(set(raw), {0})

    def test_ambiguous_nonzero_start_does_not_admit_audio_past_recording_end(self):
        shifted = self.root / "shifted.mkv"
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=3",
             "-af", "asetpts=PTS+5/TB", "-c:a", "pcm_s16le", shifted])
        info = probe(shifted, timeout=30)
        self.assertEqual(float(info["probe"]["format"]["start_time"]), 5)
        self.assertEqual(info["duration"], 8)
        with self.assertRaisesRegex(ValueError, "Cannot determine recording-relative duration"):
            source_duration(info)
        self.assertEqual(info["duration"], 8)  # Historical review probing is unchanged.

    def test_exact_thirty_seconds_with_decimal_endpoints(self):
        long_source = self.root / "long.wav"
        run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "sine=frequency=440:duration=33",
             "-c:a", "pcm_s16le", long_source])
        folder = self.root / "thirty-seconds"
        folder.mkdir()
        clip = prepare_clip(long_source, dict(start=2.002, end=32.002), [0], folder)
        raw, duration = wav_info(clip)
        self.assertEqual(duration, 30)
        self.assertNotEqual(set(raw), {0})


class ExtractionValidation(unittest.TestCase):
    def test_invalid_intervals_and_selections_fail_before_processing(self):
        with tempfile.TemporaryDirectory() as folder, patch("agent_speech_processor.clips.run") as invoked:
            for start, end in ((-1, 1), (0, 0), (2, 1), (0, 30.001),
                               (float("nan"), 1), (0, float("inf")), (True, 2), ("0", 1)):
                with self.subTest(start=start, end=end), self.assertRaises(ValueError):
                    prepare_clip("unused", dict(start=start, end=end), [0], folder)
            for indices in ([], [-1], [0, 0], [0, 1, 2, 3, 4], [True], [0.5], [[0]]):
                with self.subTest(indices=indices), self.assertRaises(ValueError):
                    prepare_clip("unused", dict(start=0, end=1), indices, folder)
            invoked.assert_not_called()

    def test_probe_supports_a_short_playback_deadline(self):
        response = dict(format=dict(duration="10"), streams=[dict(codec_type="audio")])
        with patch("agent_speech_processor.audio.run", return_value=subprocess.CompletedProcess(
                [], 0, json.dumps(response).encode(), b"")) as invoked:
            media = probe("unused", timeout=30)
            self.assertEqual(source_duration(media), 10)
        self.assertEqual(invoked.call_args.kwargs["timeout"], 30)

    def test_recording_extent_includes_late_audio_and_video_streams(self):
        media = dict(duration=99, probe=dict(format=dict(start_time="5"), streams=[
            dict(codec_type="audio", start_time="5", duration="3"),
            dict(codec_type="audio", start_time="6", duration="4"),
            dict(codec_type="video", start_time="5.5", duration="6"),
            dict(codec_type="subtitle")]))
        self.assertEqual(source_duration(media), 6.5)
        for invalid in (None, "nan", "inf", "0", "-1"):
            media["probe"]["streams"][2]["duration"] = invalid
            with self.subTest(duration=invalid), self.assertRaisesRegex(ValueError, "every audio/video"):
                source_duration(media)
        media["probe"]["streams"][2]["duration"] = "6"
        for invalid in (None, "nan", "inf"):
            media["probe"]["streams"][2]["start_time"] = invalid
            with self.subTest(start=invalid), self.assertRaisesRegex(ValueError, "every audio/video"):
                source_duration(media)


if __name__ == "__main__":
    unittest.main()

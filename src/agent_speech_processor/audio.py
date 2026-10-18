"""Shared recording timeline, PCM identity and FFmpeg extraction recipes."""
import hashlib
import json
import math
from pathlib import Path
import shutil
import tempfile
import wave

from .processes import run
from .records import checksum

RATE = 16000
CHANNELS = 1
SAMPLE_WIDTH = 2
RESERVE = 2 * 1024**3


def probe(source, *, timeout=300):
    data = json.loads(run(["ffprobe", "-v", "error", "-show_format", "-show_streams",
                           "-of", "json", source], timeout=timeout).stdout)
    streams = [s for s in data["streams"] if s["codec_type"] == "audio"]
    duration = float(data["format"]["duration"])
    if not streams or not math.isfinite(duration) or duration <= 0:
        raise ValueError("Recording needs audio and a finite positive duration")
    return {"duration": duration, "streams": streams, "probe": data}


def source_duration(media):
    """Return the playback extent after FFmpeg normalizes the container origin.

    For nonzero origins, container duration can mean either elapsed time or an
    absolute endpoint. Use explicit audio/video stream timing instead; ambiguous
    metadata must not turn an out-of-recording request into padded silence.
    Used by passage playback and new drafts, not historical review receipts.
    """
    data = media["probe"]
    error = ("Cannot determine recording-relative duration for this nonzero-start recording: "
             "every audio/video stream needs a finite start and positive duration")
    try:
        origin = float(data["format"].get("start_time", 0))
        if not math.isfinite(origin):
            raise ValueError("Recording start time must be finite")
        if origin == 0:
            return media["duration"]
        endpoints = []
        for stream in data["streams"]:
            if stream.get("codec_type") not in ("audio", "video"):
                continue
            start, duration = float(stream["start_time"]), float(stream["duration"])
            if not all(math.isfinite(t) for t in (start, duration)) or duration <= 0:
                raise ValueError(error)
            endpoints.append(math.fsum((start, -origin, duration)))
        duration = max(endpoints)
        if not math.isfinite(duration) or duration <= 0:
            raise ValueError(error)
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise ValueError(error) from exc
    return duration


def wav_info(path):
    with wave.open(str(path)) as audio:
        if (audio.getnchannels(), audio.getsampwidth(), audio.getframerate()) != (CHANNELS, SAMPLE_WIDTH, RATE):
            raise ValueError(f"Expected mono 16-bit 16 kHz PCM: {path}")
        frames = audio.getnframes()
        raw = audio.readframes(frames)
    if frames == 0 or len(raw) != frames * SAMPLE_WIDTH:
        raise ValueError(f"Empty or truncated WAV: {path}")
    return raw, frames / RATE


def aligned_filter(start, end, length, *, mono=False):
    """Preserve recording delay, pad early endings, and trim to exact PCM length."""
    filters = (f"atrim=start={start}:end={end},asetpts=PTS-{start}/TB,"
               f"aresample={RATE}:async=1:first_pts=0,")
    if mono:
        filters += "aformat=channel_layouts=mono,"
    return filters + f"apad=whole_dur={length},atrim=duration={length}"


def mix_filter(count):
    return f"amix=inputs={count}:duration=longest:dropout_transition=0:normalize=1"


def pcm_output(wav):
    return ["-vn", "-ar", RATE, "-ac", str(CHANNELS), "-c:a", "pcm_s16le", wav]


def stream_command(source, start, end, index, wav, *, length):
    return ["ffmpeg", "-v", "error", "-nostdin", "-n", "-copyts", "-start_at_zero",
            "-ss", start, "-i", source, "-map", f"0:a:{index}",
            "-af", aligned_filter(start, end, length), "-t", length, *pcm_output(wav)]


def audio_input_command(source, start, end, indices, wav):
    """Build a direct draft mix using the same aligned filter as short clips."""
    length = end - start
    command = ["ffmpeg", "-v", "error", "-nostdin", "-n", "-copyts", "-start_at_zero",
               "-ss", start, "-i", source, "-t", length]
    filters = [f"[0:a:{index}]" + aligned_filter(start, end, length, mono=True) + f"[s{position}]"
               for position, index in enumerate(indices)]
    output = "[s0]"
    if len(indices) > 1:
        inputs = "".join(f"[s{position}]" for position in range(len(indices)))
        filters.append(inputs + mix_filter(len(indices)) + "[out]")
        output = "[out]"
    return command + ["-filter_complex", ";".join(filters), "-map", output, *pcm_output(wav)]


def check_space(folder, seconds, keep_audio=False):
    # One bounded PCM plus publication/staging copies, with a generous text budget.
    need = RESERVE + math.ceil(seconds * RATE * SAMPLE_WIDTH * (3 if keep_audio else 1)) + 16 * 1024**2
    folder = Path(folder).absolute()
    while not folder.exists():
        folder = folder.parent
    for destination in (folder, Path(tempfile.gettempdir())):
        if shutil.disk_usage(destination).free < need:
            raise RuntimeError("Not enough free disk for the next draft chunk and 2 GiB reserve; completed work is retained")


def pcm_identity(wav):
    raw, duration = wav_info(wav)
    return {"sha256": hashlib.sha256(raw).hexdigest(), "frames": len(raw) // SAMPLE_WIDTH,
            "duration_seconds": duration, "sample_rate": RATE,
            "channels": CHANNELS, "sample_width_bytes": SAMPLE_WIDTH, "wav_sha256": checksum(wav)}

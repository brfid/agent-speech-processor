"""Short review windows and optional voice-activity alarms."""
import array
import math
import sys

from ..audio import RATE, wav_info
from ..clips import prepare_audio
from ..records import checksum


def windows(duration, core=20, context=2):
    if not all(math.isfinite(x) for x in (duration, core, context)) or not (
            duration > 0 and core > 0 and context >= 0 and core + 2 * context <= 30):
        raise ValueError("Use positive duration/core, nonnegative context, and at most 30 seconds per clip")
    start, index = 0.0, 0
    while start < duration:
        end = min(duration, start + core)
        yield {"index": index, "core_start": start, "core_end": end,
               "start": max(0.0, start - context), "end": min(duration, end + context)}
        start, index = end, index + 1


def activity(path):
    """WebRTC VAD is only a missing-speech alarm, never proof of silence."""
    import webrtcvad
    raw, duration = wav_info(path)
    samples = array.array("h", raw)
    if sys.byteorder != "little":
        samples.byteswap()
    vad, spans, start = webrtcvad.Vad(2), [], None
    count = 480  # WebRTC's 30 ms frames at 16 kHz.
    for offset in range(0, len(samples), count):
        frame = raw[offset * 2:(offset + count) * 2].ljust(count * 2, b"\0")
        voiced = vad.is_speech(frame, RATE)
        if voiced and start is None:
            start = offset / RATE
        if not voiced and start is not None:
            spans.append([start, offset / RATE])
            start = None
    if start is not None:
        spans.append([start, duration])
    return {"method": "webrtcvad mode 2; 30ms; alarm only", "voiced": spans,
            "rms": math.sqrt(sum(x * x for x in samples) / len(samples)) / 32768,
            "clipped_fraction": sum(abs(x) >= 32760 for x in samples) / len(samples)}


def prepare_window(source, span, indices, folder):
    """Decode only a bounded window; pad late/short streams on the same timeline."""
    paths, mix = prepare_audio(source, span, indices, folder, timeout=300)
    views = [(mix, "mix")]
    if len(indices) > 1:
        views += [(p, f"0:a:{i}") for p, i in zip(paths, indices)]
    return [{"path": str(p), "sha256": checksum(p), "view": view,
             "source_start": span["start"], "source_end": span["end"],
             "activity": activity(p)} for p, view in views]

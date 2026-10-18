"""Bounded clip preparation without model analysis or evidence qualification."""
import math
from pathlib import Path
import shutil

from .audio import RATE, SAMPLE_WIDTH, RESERVE, mix_filter, pcm_output, stream_command, wav_info
from .processes import run


def prepare_audio(source, span, indices, folder, *, timeout):
    """Decode a bounded window without model analysis or evidence receipts."""
    folder = Path(folder)
    if (not indices or any(type(i) is not int or i < 0 for i in indices)
            or len(set(indices)) != len(indices)):
        raise ValueError("Select distinct nonnegative audio stream indices")
    if len(indices) > 4:
        raise ValueError("At most four separate audio streams per clip")
    start, end = span["start"], span["end"]
    if (any(type(t) not in (int, float) or not math.isfinite(t) for t in (start, end))
            or not 0 <= start < end or end - start > 30 + 1e-9):
        raise ValueError("Audio clips need finite nonnegative times and a duration of at most 30 seconds")
    # Decimal millisecond endpoints can subtract to 30.000000000000004.
    length = min(30.0, end - start)
    if shutil.disk_usage(folder).free < RESERVE + math.ceil(length * RATE * SAMPLE_WIDTH * (len(indices) + 1)):
        raise RuntimeError("Not enough free disk for the bounded clip and 2 GiB reserve")
    paths = [folder / f"stream-{index}.wav" for index in indices]
    mix = folder / "mix.wav"
    for path in [*paths, mix]:
        if path.exists() or path.is_symlink():
            raise ValueError(f"Clip output already exists: {path}")
    for index, path in zip(indices, paths):
        run(stream_command(source, start, end, index, path, length=length), timeout=timeout)
        _, actual = wav_info(path)
        if abs(actual - length) > 1 / RATE:
            raise ValueError("Prepared track duration does not match the requested window")
    command = ["ffmpeg", "-v", "error", "-nostdin", "-n"]
    for path in paths:
        command += ["-i", path]
    command += ["-filter_complex", mix_filter(len(paths)), *pcm_output(mix)]
    run(command, timeout=timeout)
    return paths, mix


def prepare_clip(source, span, indices, folder):
    """Return a mixed WAV for local playback, without VAD, models or hashing.

    The caller owns the temporary folder and validates the recording's duration.
    Each selected stream and the final mix have a 60-second process deadline.
    Track delays and early endings are padded on the recording-relative timeline.
    """
    _, mix = prepare_audio(source, span, indices, folder, timeout=60)
    return mix

"""Short source passages for ordinary reading/listening, independent of review."""
import math
from pathlib import Path
import shutil
import subprocess
import tempfile

from .records import atomic
from .inputs import prepare_sources, materialize
from .processes import run
from .clips import prepare_clip
from .audio import probe, source_duration
from .timestamps import parse_time, timecode


def source_range(start, end, context, duration):
    if not all(math.isfinite(x) for x in (start, end, context, duration)):
        raise ValueError("Range, context and recording duration must be finite")
    if not 0 <= start < end <= duration:
        raise ValueError("Use 0 <= start < end <= recording duration; only context is clipped at recording edges")
    if not 0 <= context <= 30:
        raise ValueError("Context must be between 0 and 30 seconds")
    span = {"start": max(0.0, start - context), "end": min(duration, end + context)}
    if span["end"] - span["start"] > 30 + 1e-9:
        raise ValueError("Passages including context must be at most 30 seconds; request a shorter range")
    return span


def player_command(config, clip):
    player = config.get("player") or shutil.which("afplay") or shutil.which("ffplay")
    if not player:
        raise ValueError("No local player found; configure player as an afplay or ffplay executable, or use --output")
    expanded = Path(player).expanduser()
    if not expanded.is_absolute() and expanded.name != player:
        raise ValueError("Configured player must be an absolute executable path or bare afplay/ffplay name")
    player = str(expanded)
    executable = shutil.which(player)
    if not executable or Path(player).name not in ("afplay", "ffplay"):
        raise ValueError("Configured player must be an installed afplay or ffplay executable (no shell arguments)")
    if Path(player).name == "ffplay":
        return [executable, "-nodisp", "-autoexit", "-loglevel", "error", str(clip)]
    return [executable, str(clip)]


def located(span):
    return {**span, "start_timecode": timecode(span["start"]), "end_timecode": timecode(span["end"])}


def process(args, config):
    start, end = parse_time(args.start), parse_time(args.end)
    context = args.context_seconds
    # Reject malformed/oversized requests before fetching or decoding an original.
    source_range(start, end, context, end)
    indices = args.audio_stream_indices if args.audio_stream_indices is not None else [0]
    if (not indices or len(indices) > 4 or len(set(indices)) != len(indices)
            or any(type(i) is not int or i < 0 for i in indices)):
        raise ValueError("Select one to four distinct nonnegative audio stream indices")
    source = prepare_sources([args.source])[0]
    output = Path(args.output).expanduser().absolute() if args.output else None
    if output:
        if output.suffix.lower() != ".wav":
            raise ValueError("--output must name a new .wav file")
        if output.exists() or output.is_symlink():
            raise ValueError("Output already exists; refusing to overwrite: " + str(output))
        if not output.parent.is_dir():
            raise ValueError("Output parent directory must already exist")
    if args.play:
        player_command(config, "clip.wav")  # Diagnose missing player before fetching.
    materialize([source], args.fetch)
    media = probe(source, timeout=30)
    span = source_range(start, end, context, source_duration(media))
    if max(indices) >= len(media["streams"]):
        raise ValueError("Selected audio stream is missing; indices count audio streams from zero")
    with tempfile.TemporaryDirectory(prefix="interview-passage-") as temporary:
        clip = prepare_clip(source, span, indices, Path(temporary))
        if args.play:
            try:
                run(player_command(config, clip), timeout=span["end"] - span["start"] + 5)
            except subprocess.TimeoutExpired as exc:
                raise RuntimeError("Playback timed out; player stopped and temporary audio removed") from exc
        else:
            atomic(output, clip.read_bytes(), replace=False)
    return {"source": str(source), "requested": located({"start": start, "end": end}),
            "actual": located(span), "audio_stream_indices": indices,
            "duration_seconds": span["end"] - span["start"],
            "clip_path": str(output) if output else None, "played": args.play}

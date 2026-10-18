"""Local timecoded machine drafts; never assert speaker identity or audio review."""
import json
import math
from pathlib import Path
import shutil
import time
from .job import job, validate_audio_receipt
from .processes import run
from .inputs import prepare_sources
from .records import json_bytes, atomic, now, digest
from .audio import RATE, audio_input_command, check_space, pcm_identity, source_duration
from .drafts import windows, segments, draft_completion
from .retention import disposed_outputs
from .provenance import recipe

OVERLAP_SECONDS = 5


def stream_indices(args):
    indices = getattr(args, "audio_stream_indices", None) or [0]
    if any(index < 0 for index in indices) or len(indices) != len(set(indices)):
        raise ValueError("Audio stream indices must be distinct nonnegative integers")
    return indices


def recognition_command(engine, model, wav, prefix, language, max_context, gpu):
    return [str(x) for x in [engine, "-m", model, "-f", wav, "-l", language,
                            "-t", 4, "-p", 1, "-oj", "-osrt", "-otxt", "-of", prefix,
                            *([] if max_context is None else ["-mc", max_context]),
                            *([] if gpu else ["-ng"])]]


def recognize(command, stage, timeout):
    """Raw files go straight to staging; capture bounded diagnostics before parsing."""
    path = stage / "recognition.json"
    diagnostic = {"command": [str(x) for x in command], "started_at": now(), "status": "running"}
    atomic(path, json_bytes(diagnostic), replace=False)
    started = time.monotonic()
    result = None
    try:
        result = run(command, timeout=timeout, check=False)
    except BaseException as exc:
        result = exc
        diagnostic["error"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        diagnostic.update(status="returned" if not isinstance(result, BaseException) else "interrupted_or_failed",
                          elapsed_seconds=time.monotonic() - started, returncode=getattr(result, "returncode", None))
        for stream in ("stdout", "stderr"):
            data = getattr(result, stream, None) or b""
            if isinstance(data, str):
                data = data.encode()
            diagnostic[stream] = data[-16384:].decode(errors="replace")
            diagnostic[stream + "_truncated"] = len(data) > 16384
        atomic(path, json_bytes(diagnostic))
    if result.returncode:
        raise RuntimeError(f"Recognizer failed ({result.returncode}): " + diagnostic["stderr"][-2000:])


def validate_recognition(files, details):
    record = json.loads(files[details["json"]].read_text())
    if not isinstance(record, dict) or not isinstance(record.get("transcription"), list):
        raise ValueError("Recognizer response must contain a transcription list")
    duration = details["end_seconds"] - details["start_seconds"]
    for item in record["transcription"]:
        if (not isinstance(item, dict) or not isinstance(item.get("offsets"), dict)
                or not isinstance(item.get("text"), str)):
            raise ValueError("Recognizer segments need numeric offsets and text")
        if any(type(item["offsets"][key]) not in (int, float) for key in ("from", "to")):
            raise ValueError("Recognizer timestamps must be finite numbers")
    verified = segments(record, duration)
    previous = -1
    for start, end, words in verified:
        if start < previous or start >= duration or end <= start or not words:
            raise ValueError("Recognizer returned reversed/empty or out-of-source segments")
        previous = start
    return {**details, "segments": len(verified), "silence_or_empty_draft": not bool(verified)}


def chunk_name(source, start, end):
    return f"{Path(source).name}.{round(start * 1000):010}-{round(end * 1000):010}"


def recognition_pending(state, source):
    steps = state["steps"]
    if any(steps.get(name, {}).get("status") != "done" for name in ("media-probe", "replay-recipe")):
        return True
    settings = state["specification"]["settings"]
    spans = windows(steps["media-probe"]["details"]["duration"], settings["chunk_seconds"], settings["overlap_seconds"])
    for start, end in spans:
        step = steps.get(chunk_name(source, start, end), {})
        if step.get("status") != "done" and not (step.get("prepared") and not step.get("rejected")):
            return True
    return False


def process(args):
    from .transcripts import draft_text, initialize_edited
    sources = prepare_sources([args.source])
    indices = stream_indices(args)
    max_context = getattr(args, "max_context", 0)
    keep_audio = getattr(args, "keep_audio", False)
    if max_context is not None and max_context < 0:
        raise ValueError("--max-context must be nonnegative")
    settings = {"engine": "whisper-cli", "language": args.language,
                "chunk_seconds": args.chunk_seconds, "overlap_seconds": OVERLAP_SECONDS,
                "audio_stream": ",".join(f"0:a:{index}" for index in indices),
                "pcm": f"{RATE}Hz mono s16le",
                "adapter_version": 4, "recipe_version": 2,
                "retention": {"wav": "retained" if keep_audio else "temporary", "raw_json": "required"},
                "audio_timeline": "recording-relative; preserve stream delays and pad each window"}
    if len(indices) > 1:
        settings["audio_mix"] = "mono streams, equal average, aligned and padded to window"
    if max_context is not None:
        settings["max_context"] = max_context
    settings["device"] = "gpu" if args.gpu else "cpu"
    source = sources[0]
    ledger = Path(args.output).expanduser() / ".processing.json"
    if ledger.is_symlink():
        raise ValueError("Bookkeeping files must not be symlinks")
    saved = json.loads(ledger.read_text()) if ledger.exists() else None
    reuse_only = False
    if saved:
        validate_audio_receipt(saved)
        old = saved["specification"]["settings"]
        if (any(old.get(k) != v for k, v in settings.items())
                or old.get("max_context") != max_context
                or (args.model and Path(args.model).name != old.get("model"))):
            raise ValueError("Inputs/settings differ from this job. Use a new output folder.")
        reuse_only = not recognition_pending(saved, source)
    engine = model = replay = snapshot = None
    if reuse_only:
        settings = saved["specification"]["settings"]
    else:
        check_space(args.output, args.chunk_seconds + 2 * OVERLAP_SECONDS, keep_audio)
        if not args.model:
            raise ValueError("--model is required when recognition remains")
        model = Path(args.model).expanduser().absolute()
        if not model.is_file():
            raise FileNotFoundError("Whisper model not installed: " + str(model))
        engine = shutil.which("whisper-cli")
        if not engine:
            raise FileNotFoundError("Install whisper.cpp to provide whisper-cli")
        replay, snapshot = recipe(engine, model, getattr(args, "model_receipt", None))
        replay["commands"] = {
            "probe": ["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", "{source}"],
            "recognition": recognition_command("{whisper-cli}", "{model}", "{wav}", "{output-prefix}",
                                               args.language, max_context, args.gpu),
            "extraction": "Exact per-window arguments are retained in each chunk's receipt details; substitute {source} and {wav}."}
        settings.update(model=model.name, model_sha256=replay["model"]["sha256"],
                        engine_sha256=replay["tools"]["whisper-cli"]["sha256"])
        replay["compatibility"] = {"schema": 1, "code_sha256": replay["draft_code_sha256"],
                                   "model": {k: replay["model"][k] for k in ("sha256", "bytes")},
                                   "tools": replay["tools"], "environment": replay["environment"],
                                   "settings": dict(settings), "commands": replay["commands"]}
        settings["compatibility_sha256"] = digest(replay["compatibility"])
    with job(args.output, "audio", sources, settings, args.fetch, relative_sources=True, missing_outputs=disposed_outputs) as current:
        expected_source = getattr(args, "source_sha256", None)
        if expected_source and current.source_checksum(source) != expected_source:
            raise ValueError("Original does not match the requested source SHA-256")
        def save_recipe(tmp):
            if replay is None:
                raise ValueError("Saved recipe is incomplete; recognition resources are required")
            (tmp / "recipe.json").write_bytes(json_bytes(replay))
            (tmp / "recipe-source.zip").write_bytes(snapshot)
            return {name: tmp / name for name in ("recipe.json", "recipe-source.zip")}, {"recipe_sha256": digest(replay)}
        current.step("replay-recipe", save_recipe)
        def probe(tmp):
            data = run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", source]).stdout
            info = json.loads(data)
            streams = [s for s in info["streams"] if s["codec_type"] == "audio"]
            if not streams:
                raise ValueError("No audio stream found")
            if max(indices) >= len(streams):
                raise ValueError(f"Selected audio stream missing: {max(indices)}; source has {len(streams)}")
            container = info.get("format", {})
            duration = float(container.get("duration") or 0)
            duration = source_duration({"duration": duration, "probe": {**info, "format": container}})
            windows(duration, args.chunk_seconds, OVERLAP_SECONDS)  # Validate before checkpointing the probe.
            (tmp / "media.json").write_bytes(data)
            return {"media.json": tmp / "media.json"}, {"duration": duration, "audio_streams": len(streams),
                                                       "selected_streams": indices,
                                                       "channels": [streams[index].get("channels") for index in indices]}
        media = current.step("media-probe", probe)
        spans = windows(media["duration"], args.chunk_seconds, OVERLAP_SECONDS)
        all_chunks, newly_processed = [], 0
        for start, end in spans:
            name = chunk_name(source, start, end)
            done = current.completed(name)
            if args.limit_chunks is not None and newly_processed >= args.limit_chunks and not done:
                break
            def transcribe(tmp, stage, start=start, end=end, name=name):
                if engine is None:
                    raise ValueError("Recognition is still required; rerun with its pinned model and tools")
                check_space(current.folder, end - start, keep_audio)
                wav = tmp / "audio.wav"
                run(audio_input_command(source, start, end, indices, wav))
                pcm = pcm_identity(wav)
                if abs(pcm["duration_seconds"] - (end - start)) > 1 / RATE:
                    raise ValueError("Prepared PCM does not cover the requested source window")
                details = {"start_seconds": start, "end_seconds": end,
                           "json": f"chunks/{name}.json", "pcm": pcm,
                           "extract_argv": [str(x) for x in audio_input_command("{source}", start, end, indices, "{wav}")]}
                atomic(stage / "input.json", json_bytes(details), replace=False)
                recognize(recognition_command(engine, model, wav, stage / "draft", args.language, max_context, args.gpu),
                          stage, timeout=max(3600, math.ceil((end-start)*60)))
                files = {f"chunks/{name}.{suffix}": stage / ("draft." + suffix)
                         for suffix in ("json", "srt", "txt")}
                files[f"diagnostics/{name}.json"] = stage / "recognition.json"
                if keep_audio:
                    files[f"chunks/{name}.wav"] = wav
                return files, details
            all_chunks.append(current.step(name, transcribe, validate=validate_recognition))
            newly_processed += not done
            print(json.dumps({"chunk": name, "status": "draft", "reused": done}), flush=True)
        if len(all_chunks) == len(spans):
            def combine(tmp):
                text = draft_text(source, current.folder, media["duration"], indices, all_chunks)
                (tmp / "transcript-draft.md").write_text(text)
                return {"transcript-draft.md": tmp / "transcript-draft.md"}, {"audio_checked": False}
            current.step("combined-draft", combine)
            editorial = initialize_edited(current, source, media["duration"], indices, all_chunks, fetch=args.fetch)
        else:
            editorial = None
        mix_note = "Separate tracks were mixed equally after mono downmix." if len(indices) > 1 else ""
        current.write_readme(
            f"# {current.folder.name}\n\n"
            "Session date and participants: unknown until supported by evidence.\n\n"
            f"Original: [{source.name}](<{source}>)\n\n"
            f"Audio streams selected: {', '.join(f'0:a:{index}' for index in indices)}. {mix_note}\n\n"
            f"Machine draft: whisper.cpp, `{settings['model']}`, {settings['device']} mode, "
            f"text context {'default' if max_context is None else max_context}. "
            "Absolute recording times appear in both Markdown transcripts; chunk SRT/JSON times are local, "
            "with absolute start/end in chunk filenames. Overlap is retained for listening review.\n\n"
            "Audio-checked coverage: none. Record checked spans, reviewer/method, speaker evidence, "
            "gaps and uncertain words here. Preserve `transcript-draft.md` as the machine baseline. "
            "Use `transcript-edited.md` for editing and manual HTML interpretation notes; reruns preserve it. "
            "Its initial mechanical edit is not audio review. `recipe.json` and `recipe-source.zip` "
            "retain the tool/model/runtime identities and exact processing source. "
            "WAV chunks are temporary unless explicitly retained; PCM hashes and extraction arguments are recorded. "
            "Reading and source playback do not require draft verification.\n")
        return {"chunks_drafted": len(all_chunks), "chunks_required": len(spans),
                "machine_draft_complete": len(all_chunks) == len(spans), "audio_checked": False,
                "output": str(current.folder), "editorial": editorial,
                "draft_completion": draft_completion(current)}

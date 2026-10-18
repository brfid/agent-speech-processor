"""Whisper chunk planning, raw segments and read-only draft receipts."""
import json
import math
import os
from pathlib import Path

from .records import checksum, digest
from .verification import recorded_job


def windows(duration, chunk, overlap):
    if not math.isfinite(duration) or duration <= 0 or chunk <= 0 or not 0 <= overlap < chunk / 2:
        raise ValueError("Invalid duration, chunk length or overlap")
    return [(max(0, start - overlap), min(duration, start + chunk + overlap))
            for start in range(0, math.ceil(duration), chunk)]


def segments(record, duration):
    output = []
    for segment in record["transcription"]:
        offsets = segment["offsets"]
        start, end = offsets["from"] / 1000, offsets["to"] / 1000
        if not 0 <= start <= end <= duration + 1:
            raise ValueError("Recognizer returned out-of-range segment timestamps")
        output.append((start, end, segment["text"].strip()))
    return output


def draft_completion(current):
    """Report coverage and file presence without hashing user-owned text."""
    state, folder = current.state, current.folder
    if state["specification"]["kind"] != "audio":
        return None
    steps, settings = state["steps"], state["specification"]["settings"]
    media = steps.get("media-probe", {})
    expected = (windows(media["details"]["duration"], settings["chunk_seconds"], settings["overlap_seconds"])
                if media.get("status") == "done" else None)
    completed = {(s["details"]["start_seconds"], s["details"]["end_seconds"])
                 for s in steps.values() if s.get("status") == "done"
                 and s.get("details", {}).get("json") in s.get("outputs", {})}
    count = len(completed.intersection(expected)) if expected is not None else 0
    baseline = folder / "transcript-draft.md"
    edited = folder / "transcript-edited.md"
    baseline_present = baseline.is_file() and not baseline.is_symlink()
    edited_present = edited.is_file() and not edited.is_symlink()
    machine_complete = (expected is not None and count == len(expected) and baseline_present
                        and steps.get("combined-draft", {}).get("status") == "done")
    edited_required = True
    # Canonical text is user-owned and independent of recognition completeness.
    # Read mapping controls and file metadata only; never gate it on audio bytes,
    # machine receipts, or its initial seed hash.
    from .working import SIDECAR, resolve
    registered = (folder / SIDECAR).exists() or (folder / SIDECAR).is_symlink()
    working_path, working_present, working_error = edited, edited_present, None
    try:
        keys = state["specification"].get("sources", [])
        source = (folder / keys[0]).resolve() if len(keys) == 1 else None
        canonical = resolve(folder, expected_source=source, require=False)
        if canonical:
            working_path, working_present = canonical["transcript"], canonical["available"]
            if not working_present:
                working_error = "Registered working transcript is missing; restore the user-owned file instead of regenerating it"
    except (OSError, ValueError, RuntimeError) as exc:
        working_path, working_present, working_error = None, False, str(exc)
    working_required = edited_required or registered
    return {"expected_chunks": len(expected) if expected is not None else None,
            "completed_chunks": count, "combined_baseline_present": baseline_present,
            "edited_transcript_present": edited_present, "edited_transcript_required": edited_required,
            "working_transcript": str(working_path) if working_path is not None else None,
            "working_transcript_present": working_present, "working_transcript_required": working_required,
            "working_transcript_registered": registered, "working_transcript_error": working_error,
            "machine_draft_complete": machine_complete,
            "complete": machine_complete and (working_present or not working_required)}


def inside(folder, relative):
    part = Path(relative)
    path = folder / part
    if part.is_absolute() or ".." in part.parts or path.is_symlink() or not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Draft receipt contains an unsafe output path")
    return path


def import_draft(folder, source, expected_source, expected_recipe):
    folder, source = Path(folder).resolve(), Path(source).resolve()
    receipt = folder / ".processing.json"
    state = json.loads(receipt.read_text())
    specification = state.get("specification", {})
    from .job import validate_audio_receipt
    validate_audio_receipt(state)
    source_key = os.path.relpath(source, folder)
    if specification.get("sources") != [source_key]:
        raise ValueError("Draft belongs to another original recording")
    if state["sources"][source_key]["sha256"] != expected_source:
        raise ValueError("Draft source hash differs from the session manifest")
    if digest(specification["settings"]) != expected_recipe:
        raise ValueError("Draft recipe hash differs from the session manifest")
    steps = state["steps"]
    if steps.get("combined-draft", {}).get("status") != "done":
        raise ValueError("A complete machine draft is required before review")
    current = recorded_job(folder, state)
    for name, step in steps.items():
        if step.get("status") != "done":
            raise ValueError(f"Incomplete draft step: {name}")
        for relative, expected in step["outputs"].items():
            inside(folder, relative)
            try:
                current.verify_output(relative, expected)
            except ValueError as exc:
                raise ValueError(f"Modified draft output: {relative}") from exc
    duration = steps["media-probe"]["details"]["duration"]
    settings = specification["settings"]
    expected_windows = windows(duration, settings["chunk_seconds"], settings["overlap_seconds"])
    chunks = sorted((s["details"] for s in steps.values() if "json" in s.get("details", {})),
                    key=lambda d: d["start_seconds"])
    actual = [(d["start_seconds"], d["end_seconds"]) for d in chunks]
    if actual != expected_windows:
        raise ValueError("Draft chunks do not exactly cover the recorded recipe")
    transcript = []
    for index, item in enumerate(chunks):
        start, end = actual[index]
        core_start, core_end = index * settings["chunk_seconds"], min(duration, (index + 1) * settings["chunk_seconds"])
        previous = -1
        for a, b, text in segments(json.loads(inside(folder, item["json"]).read_text()), end - start):
            if a < previous or a == b or not text:
                raise ValueError("Draft chunk has reversed/empty segments")
            previous = a
            a, b = start + a, min(duration, start + b)
            # Ownership is by midpoint; context remains available in raw chunks.
            if core_start <= (a + b) / 2 < core_end and a < b:
                transcript.append({"start": a, "end": b, "text": text,
                                   "speaker": "unknown", "chunk": item["json"]})
    return {"duration": duration, "segments": transcript, "chunks": len(chunks),
            "receipt_sha256": checksum(receipt), "recipe_sha256": expected_recipe,
            "source_sha256": expected_source}

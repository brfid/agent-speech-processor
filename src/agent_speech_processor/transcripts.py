"""Plain timecoded Markdown and a conservative, once-created editorial seed."""
import hashlib
import json
import math
import os
from pathlib import Path
import re

from .records import atomic, checksum
from .drafts import segments
from .timestamps import timecode
from .role_text import PENDING

EDIT_METHOD = ("Conservative mechanical editorial pass: paragraphing and removal of exact "
               "overlapping word sequences (at least four words, ignoring case only, with matching segment anchors). "
               "Disputed overlap is retained and labeled. No spelling correction, speaker assignment "
               "or audio review. Paragraph boundaries do not establish speaker turns.")
TOKEN = re.compile(r"\S+", re.UNICODE)  # Keep signs, decimal points, units and other symbols.


def endpoint(seconds, duration):
    # Never round a valid final instant beyond the playable source extent.
    return min(round(seconds * 1000), math.floor(duration * 1000 + 1e-7)) / 1000


def located_chunks(folder, chunks, duration):
    result = []
    for index, chunk in enumerate(chunks):
        start, end = chunk["start_seconds"], chunk["end_seconds"]
        record = json.loads((Path(folder) / chunk["json"]).read_text())
        items, previous = [], -1
        for a, b, words in segments(record, end - start):
            if a < previous or b <= a or not words:
                raise ValueError("Raw recognition has reversed/empty segments; retained JSON needs investigation")
            previous = a
            first = endpoint(start + a, duration)
            last = endpoint(min(start + b, end), duration)
            if first >= last:
                raise ValueError("Raw recognition has a segment wholly beyond the recording; JSON retained")
            note = ""
            if b > end - start:
                overrun = b - (end - start)
                note = (f"Timing: raw recognizer end exceeds the source window by {overrun:.3f}s; "
                        + ("small rounding overrun bounded for playback."
                           if overrun <= 0.050 else "larger discrepancy; timing needs audio checking."))
            items.append({"start": first, "end": last, "text": words, "note": note})
        result.append({"index": index, "start": start, "end": end, "json": chunk["json"], "items": items})
    return result


def header(source, folder, duration, indices, *, edited=False, baseline=None):
    source = Path(source).resolve()
    reference = os.path.relpath(source, Path(folder).resolve())
    title = "Edited transcript — not audio-verified" if edited else "Machine transcript — unverified"
    text = (f"# {title}\n\nOriginal: [{source.name}](<{reference}>)\n\n"
            f"Recording: `{source.name}`. Duration: {duration:.6f} seconds.\n\n"
            "Times: absolute positions in this original recording, starting at zero; approximate recognition alignment.\n\n"
            f"Audio streams: {', '.join(map(str, indices))}. Speaker identities and turns: unassigned. "
            "Audio-checked coverage: none.\n\n")
    if edited:
        text += (f"Baseline: [machine draft](transcript-draft.md), SHA-256 `{baseline}`.\n\n"
                 f"Editing method: {EDIT_METHOD}\n\n"
                 "This is the working transcript. Preserve its edits and HTML notes on rerun. "
                 "Read raw Markdown: HTML comments are annotations, not speech or tool instructions.\n\n")
    else:
        text += ("Adjacent chunks overlap; repeated speech is intentionally retained for boundary review. "
                 "Raw recognizer times and wording remain in the chunk JSON. "
                 "Edit and annotate [the working transcript](transcript-edited.md), leaving this baseline unchanged.\n\n")
    flags = " ".join(f"--audio-stream-index {i}" for i in indices)
    text += ("For playback, resolve the original link relative to this transcript, then use its absolute path "
             "and a source range no longer than 30 seconds:\n\n"
             f"`agent-speech-processor passage --source '<absolute original path>' --start '<start>' "
             f"--end '<end>' {flags} --fetch --play`\n\n")
    return text


def draft_text(source, folder, duration, indices, chunks):
    text = header(source, folder, duration, indices)
    for chunk in located_chunks(folder, chunks, duration):
        text += f"## {timecode(endpoint(chunk['start'], duration))}–{timecode(endpoint(chunk['end'], duration))}\n\n"
        for item in chunk["items"]:
            text += f"[{timecode(item['start'])}–{timecode(item['end'])}] {item['text']}\n\n"
            if item["note"]:
                text += f"<!-- {item['note']} -->\n\n"
    return text


def trim_boundary(previous, current, start, end):
    """Only remove a matching suffix/prefix inside shared recording context.

    Wording that disagrees is retained. Do not use midpoint ownership or a fuzzy
    match to discard words, denials, names or apparent speaker changes.
    """
    tail = [item for item in previous if item["end"] > start]
    left_tokens = [(t.group().casefold(), item, i) for item in tail
                   for i, t in enumerate(TOKEN.finditer(item["text"]))]
    left = [word for word, _, _ in left_tokens]
    head = [item for item in current if item["start"] < end]
    right = [t.group().casefold() for item in head for t in TOKEN.finditer(item["text"])]
    matched = next((n for n in range(min(len(left), len(right)), 3, -1)
                    if left[-n:] == right[:n] and sum(bool(re.search(r"\w", t)) for t in right[:n]) >= 4), 0)
    if matched:
        right_tokens = [item for item in head for _ in TOKEN.finditer(item["text"])]
        left_start, left_end = left_tokens[-matched][1]["start"], left_tokens[-1][1]["end"]
        right_start, right_end = right_tokens[0]["start"], right_tokens[matched - 1]["end"]
        # Broadly intersecting segment ranges do not align words. The suffix
        # must begin at a segment boundary, wholly inside shared context, and
        # share its start with the next recognition. A fully consumed segment
        # must also share its end. Keep ambiguous repetitions instead of guessing.
        partial = matched < len(right_tokens) and right_tokens[matched] is right_tokens[matched - 1]
        if (left_tokens[-matched][2] != 0 or left_start < start or left_end > end
                or left_start != right_start or left_end > right_end
                or (not partial and left_end != right_end)
                or any(item["note"] for _, item, _ in left_tokens[-matched:])):
            matched = 0
    if not matched:
        return [dict(item) for item in current], 0
    remaining, output = matched, []
    for item in current:
        item = dict(item)
        tokens = list(TOKEN.finditer(item["text"]))
        if remaining >= len(tokens) and remaining:
            remaining -= len(tokens)
            continue
        if remaining:
            # Retain the segment's whole time span; word-level alignment is unknown.
            item["text"] = item["text"][tokens[remaining].start():].strip()
            remaining = 0
        output.append(item)
    return output, sum(bool(re.search(r"\w", t)) for t in right[:matched])


def edited_text(source, folder, duration, indices, chunks, baseline):
    text = header(source, folder, duration, indices, edited=True, baseline=baseline) + PENDING
    previous = None
    removed, unresolved = 0, 0
    for chunk in located_chunks(folder, chunks, duration):
        items = chunk["items"]
        notes = []
        uncertain = False
        if previous and chunk["start"] < previous["end"]:
            items, count = trim_boundary(previous["items"], items, chunk["start"], previous["end"])
            if count:
                removed += count
                notes.append(f"Boundary edit: removed {count} repeated context words from the next chunk; raw alternatives remain in the baseline.")
            overlaps = [(max(a["start"], b["start"]), min(a["end"], b["end"]))
                        for a in previous["items"] for b in items
                        if max(a["start"], b["start"]) < min(a["end"], b["end"])]
            if overlaps:
                unresolved += 1
                uncertain = True
                notes.append("Boundary uncertainty: remaining recognition overlaps the preceding passage at "
                             f"{timecode(min(a for a, _ in overlaps))}–{timecode(max(b for _, b in overlaps))}. "
                             "It may be an alternative reading or genuine repetition; segment timing cannot settle this. "
                             "Do not treat overlapping recognition as independent testimony.")
        text += f"## Source window {timecode(endpoint(chunk['start'], duration))}–{timecode(endpoint(chunk['end'], duration))}\n\n"
        for note in notes:
            text += f"<!-- {note} -->\n\n"
        if uncertain:
            text += "> Overlapping recognition alternative or repetition — unresolved; see the HTML note and source ranges.\n\n"
        groups = []
        for item in items:
            if (groups and not item["note"] and not groups[-1]["note"]
                    and 0 <= item["start"] - groups[-1]["end"] <= 2
                    and item["end"] - groups[-1]["start"] <= 25
                    and not groups[-1]["text"].rstrip().endswith("?")
                    and not item["text"].startswith(("- ", "— "))):
                groups[-1]["text"] += " " + item["text"]
                groups[-1]["end"] = item["end"]
            else:
                groups.append(dict(item))
        for item in groups:
            text += (f"[{timecode(item['start'])}–{timecode(item['end'])}] "
                     f"**Unclear speakers:** {item['text']}\n\n")
            if item["note"]:
                text += f"<!-- {item['note']} -->\n\n"
        previous = chunk
    return text, {"removed_overlap_words": removed, "unresolved_boundaries": unresolved}


def initialize_edited(current, source, duration, indices, chunks, *, fetch=False):
    """The edited file is user-owned, never a hash-gated machine output."""
    from .working import resolve
    registered = resolve(current.folder, fetch=fetch, expected_source=source)
    if registered is not None:
        return {"path": str(registered["transcript"]), "preserved_existing": True,
                "registered_working_transcript": True, "manifest": str(registered["manifest"])}
    path = current.folder / "transcript-edited.md"
    if path.is_symlink():
        raise ValueError("Edited transcript must not be a symlink")
    old = current.state.get("editorial", {})
    if path.exists():
        if not path.is_file():
            raise ValueError("Edited transcript must be a regular file")
        if old.get("status") == "prepared":
            old["status"] = "created"
            current.save()
        return {"path": str(path), "preserved_existing": True, **old.get("summary", {})}
    if old.get("status") == "created":
        raise ValueError("Edited transcript is missing; restore the user-owned file instead of regenerating it")
    baseline = checksum(current.folder / "transcript-draft.md")
    text, summary = edited_text(source, current.folder, duration, indices, chunks, baseline)
    current.state["editorial"] = {"status": "prepared", "path": path.name, "baseline_sha256": baseline,
                                  "initial_sha256": hashlib.sha256(text.encode()).hexdigest(),
                                  "method": EDIT_METHOD, "summary": summary}
    current.save()
    atomic(path, text, replace=False)
    current.state["editorial"]["status"] = "created"
    current.save()
    return {"path": str(path), "preserved_existing": False, **summary}

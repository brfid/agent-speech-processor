"""Human-readable evidence reports and collision-safe final publication."""
import fcntl
import json
from pathlib import Path

from ..records import atomic, checksum, digest, json_bytes
from ..timestamps import timecode


def transcript(source, report, recipe):
    status = "Automated audio review" if report["publishable"] else "Experimental audio review — not qualified"
    lines = [f"# {status}", "", f"Original: [{source.name}](<{source}>)", "",
             f"Review coverage: {report['coverage']['reviewed_seconds']:.3f} of "
             f"{report['coverage']['duration_seconds']:.3f} seconds.", "",
             f"Recipe SHA-256: `{digest(recipe)}`.", "",
             "Method: original audio mix and separate tracks supplied to a local audio-language "
             "reviewer with timed draft and diarization candidates. This is automated analysis; "
             "no human listening is claimed. Uncertainty and recognition errors may remain.", "",
             f"Reviewer: {recipe['reviewer']['model_id']} @ {recipe['reviewer']['revision']}. "
             f"Diarizer: {recipe['diarizer']['model_id']} @ {recipe['diarizer']['revision']}.", "",
             "Speaker clusters are local to each window. No cross-window or cross-recording "
             "identity is inferred from a track number, filename or documented participant.", ""]
    for result in report["windows"]:
        span = result["span"]
        lines += [f"## {timecode(span['core_start'])}–{timecode(span['core_end'])}", ""]
        if not result["accepted"]:
            lines += ["[Unreviewed: " + result["error"] + "]", ""]
            continue
        for segment in result["review"]["segments"]:
            a, b = span["start"] + segment["start"], span["start"] + segment["end"]
            cluster = segment["speaker"]
            label = "Unknown speaker" + (f" (window {span['index']}, {cluster})" if cluster != "unknown" else "")
            mark = " [uncertain]" if segment["uncertain"] else ""
            lines += [f"[{timecode(a)}–{timecode(b)}] **{label}:** {segment['text']}{mark}", ""]
        for segment in result["review"]["unresolved"]:
            a, b = span["start"] + segment["start"], span["start"] + segment["end"]
            lines += [f"[{timecode(a)}–{timecode(b)}] [Uncertain: {segment['reason']}]", ""]
    return "\n".join(lines)


def publish(derivative, content, recipe_sha256, source_sha256):
    """Own sidecar receipt; never change a draft receipt or an existing transcript."""
    derivative = Path(derivative)
    target = derivative / "transcript-reviewed.md"
    receipt = derivative / ".review-publication.json"
    lock = derivative / ".review-publication.lock"
    for path in (derivative, target, receipt, lock):
        if path.is_symlink():
            raise ValueError("Publication paths must not be symlinks")
    data = content.encode()
    import hashlib
    expected = {"schema": 1, "recipe_sha256": recipe_sha256, "source_sha256": source_sha256,
                "output_sha256": hashlib.sha256(data).hexdigest()}
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("Another review publication is active") from exc
        if receipt.exists():
            if json.loads(receipt.read_text()) != expected:
                raise ValueError("A different review owns the publication receipt")
        else:
            if target.exists():
                raise FileExistsError("An existing reviewed transcript must not be overwritten")
            atomic(receipt, json_bytes(expected), replace=False)
        if target.exists():
            if checksum(target) != expected["output_sha256"]:
                raise ValueError("Published transcript was modified; preserving it")
        else:
            atomic(target, data, replace=False)
    return str(target)

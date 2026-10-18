"""Exact-session manifests, read-only draft intake and operator reports."""
import json
from pathlib import Path
import re

from .inputs import materialize, prepare_sources
from .records import atomic, checksum, json_bytes
from .drafts import import_draft
from .audio import probe
from .verification import recorded_job


def load_manifest(path, do_fetch=False):
    path = prepare_sources([path])[0]
    materialize([path], do_fetch)
    data = json.loads(path.read_text())
    if data.get("schema") != 1 or not isinstance(data.get("recordings"), list) or not data["recordings"]:
        raise ValueError("Session manifest needs schema 1 and nonempty recordings")
    seen, destinations = set(), set()
    for entry in data["recordings"]:
        for key in ("source", "draft"):
            value = Path(entry[key]).expanduser()
            entry[key] = (path.parent / value).resolve()
        if entry["source"] in seen or entry["draft"] in destinations:
            raise ValueError("Repeated source or draft destination in session")
        seen.add(entry["source"])
        destinations.add(entry["draft"])
        for key in ("source_sha256", "draft_recipe_sha256"):
            if not re.fullmatch(r"[0-9a-f]{64}", entry[key]):
                raise ValueError(f"Missing or invalid {key}")
        indices = entry["audio_streams"]
        if not indices or len(set(indices)) != len(indices) or any(type(i) is not int or i < 0 for i in indices):
            raise ValueError("Select distinct nonnegative audio streams")
        if not entry["draft"].is_dir() or entry["source"].is_relative_to(entry["draft"]):
            raise ValueError("Existing derivative folder must be separate from originals")
    for target in destinations:
        if any(source.is_relative_to(target) for source in seen):
            raise ValueError("A derivative folder contains a selected original")
    vocabulary = data.get("vocabulary", [])
    if not isinstance(vocabulary, list) or len(vocabulary) > 100 or any(not isinstance(x, str) or len(x) > 80 for x in vocabulary):
        raise ValueError("Vocabulary must be a narrow list of at most 100 short terms")
    return path, data


def intake(entry, do_fetch):
    source, folder = entry["source"], entry["draft"]
    originals_receipt = materialize([source, folder / ".processing.json"], do_fetch)
    if checksum(source) != entry["source_sha256"]:
        raise ValueError("Original checksum does not match session provenance")
    state = json.loads((folder / ".processing.json").read_text())
    outputs = recorded_job(folder, state).required_output_paths()
    draft_receipt = materialize(outputs, do_fetch)
    draft = import_draft(folder, source, entry["source_sha256"], entry["draft_recipe_sha256"])
    media = probe(source)
    if abs(media["duration"] - draft["duration"]) > 0.1:
        raise ValueError("Original and draft durations differ")
    if max(entry["audio_streams"]) >= len(media["streams"]):
        raise ValueError("Selected audio stream is missing")
    expected = ",".join(f"0:a:{i}" for i in entry["audio_streams"])
    if state["specification"]["settings"]["audio_stream"] != expected:
        raise ValueError("Selected streams do not match the draft recipe")
    if checksum(source) != entry["source_sha256"]:
        raise ValueError("Original changed during intake")
    return draft, media, {"originals": originals_receipt, "draft_outputs": draft_receipt}


def inspect(args):
    """Assemble a read-only intake report for the selected manifest."""
    _, manifest = load_manifest(args.session, args.fetch)
    result = {"recordings": []}
    for entry in manifest["recordings"]:
        draft, media, receipts = intake(entry, args.fetch)
        result["recordings"].append({"source": str(entry["source"]), "draft": str(entry["draft"]),
                                     "source_sha256": entry["source_sha256"],
                                     "duration": draft["duration"], "chunks": draft["chunks"],
                                     "segments": len(draft["segments"]), "streams": len(media["streams"]),
                                     "draft_receipt_sha256": draft["receipt_sha256"], "fetch": receipts})
    if args.output:
        atomic(args.output, json_bytes(result), replace=False)
    return result

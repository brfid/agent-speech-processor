"""Explicit, model-free reading selections and additive human-readable guides."""
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote

from .inputs import materialize
from .records import atomic, checksum, json_bytes
from .reading_policy import BEGIN, END, disposition, validate_markers
from .publication import lock


def _regular(path):
    return path.is_file() and not path.is_symlink()


def _link(label, path, base):
    label = str(label).replace("|", "\\|").replace("[", "\\[").replace("]", "\\]")
    return f"[{label}](<{quote(os.path.relpath(path, base), safe='/')}>)"


def collect(sessions, *, fetch=False, notes=()):
    """Select the authoritative working text, independently of audio/models.

    Registered reading folders supersede historical job-local editable snapshots.
    Source identity/streams are supplied by manifests, not freshly verified here.
    """
    from . import working
    manifests = list(dict.fromkeys(Path(p).expanduser().absolute() for p in sessions))
    if not manifests or any(not _regular(p) for p in manifests):
        raise ValueError("Select existing regular session manifests")
    materialize(manifests, fetch)
    recordings, inputs, seen, seen_folders, seen_texts = [], {}, set(), set(), set()
    documents = list(dict.fromkeys(Path(p).expanduser().absolute() for p in notes))
    disposition_notes = []
    for manifest in manifests:
        data = json.loads(manifest.read_text())
        if data.get("schema") != 1 or not isinstance(data.get("recordings"), list) or not data["recordings"]:
            raise ValueError(f"Unsupported or empty session manifest: {manifest}")
        for number, item in enumerate(data["recordings"], 1):
            source = (manifest.parent / item["source"]).resolve()
            supplied_folder = manifest.parent / item["draft"]
            folder = supplied_folder.resolve()
            if not folder.is_dir() or supplied_folder.is_symlink():
                raise ValueError(f"Recording derivative folder is unavailable: {folder}")
            if source in seen:
                raise ValueError(f"Select one current variant per original recording: {source}")
            if folder in seen_folders:
                raise ValueError(f"Different recordings cannot share a derivative folder: {folder}")
            seen.add(source)
            seen_folders.add(folder)
            registered = (working.from_manifest(manifest, item, folder, fetch=fetch,
                                                  expected_source=source, require=False)
                          if "working_transcript" in item else
                          working.resolve(folder, fetch=fetch, expected_source=source, require=False))
            if registered is None:
                raise ValueError(f"Reading requires a registered canonical working transcript; promote the completed session: {folder}")
            actual_manifest = registered["manifest"]
            selected_item = registered["item"]
            for field in ("source_sha256", "draft_recipe_sha256", "audio_streams"):
                if item.get(field) != selected_item.get(field):
                    raise ValueError(f"Working registration changed recorded {field}: {folder}")
            inputs[str(actual_manifest)] = checksum(actual_manifest)
            snapshot, baseline = folder / "transcript-edited.md", folder / "transcript-draft.md"
            edited = registered["transcript"]
            if not registered["available"]:
                raise ValueError(f"Registered working transcript is missing; restore it instead of substituting historical text: {edited}")
            preferred = edited
            if preferred in seen_texts:
                raise ValueError(f"Different recordings cannot share a working transcript: {preferred}")
            seen_texts.add(preferred)
            row = {"manifest": str(actual_manifest), "order": number, "folder": str(folder),
                   "source": str(source), "source_reference": selected_item["source"],
                   "source_sha256_recorded": selected_item.get("source_sha256"),
                   "audio_streams": selected_item.get("audio_streams", []),
                   "preferred_text": str(preferred), "text_role": "working_transcript",
                   "baseline": str(baseline) if _regular(baseline) else None,
                   "edited_present": True, "registered_working_text": True,
                   "historical_edited_snapshot": str(snapshot)}
            recordings.append(row)
            if _regular(actual_manifest.parent / "README.md"):
                documents.append(actual_manifest.parent / "README.md")
            record_note = folder / "README.md"
            disposition_notes.append(record_note)
            if record_note.is_symlink() or (record_note.exists() and not record_note.is_file()):
                raise ValueError(f"Recording disposition must be a regular file: {record_note}")
    documents = list(dict.fromkeys(documents))
    if any(not _regular(p) for p in documents):
        raise ValueError("Every explicitly selected notes file must be a regular readable file")
    note_paths = list(dict.fromkeys([*documents, *(p for p in disposition_notes if _regular(p))]))
    materialize(note_paths, fetch)
    note_texts = {str(p): p.read_bytes().decode("utf-8") for p in note_paths}
    texts = {str(p): note_texts[str(p)] for p in documents}
    for row in recordings:
        note = note_texts.get(str(Path(row["folder"]) / "README.md"), "")
        try:
            row["reading_disposition"] = disposition(note)
        except ValueError as exc:
            raise ValueError(f"{exc} ({row['folder']})") from exc
        row["excluded_from_default_search"] = row["reading_disposition"] == "unusable"
    text_paths = list(dict.fromkeys(Path(r["preferred_text"]) for r in recordings
                                   if r["preferred_text"] and not r["excluded_from_default_search"]))
    materialize(text_paths, fetch)
    texts.update({str(p): p.read_bytes().decode("utf-8") for p in text_paths})
    from .role_text import status
    for row in recordings:
        if row["preferred_text"] in texts and not row["excluded_from_default_search"]:
            row["role_review"] = status(texts[row["preferred_text"]])
    return {"schema": 1, "recordings": recordings, "notes": [str(p) for p in documents],
            "manifest_hashes": inputs, "texts": texts,
            "policy_hashes": {str(p): hashlib.sha256(note_texts[str(p)].encode()).hexdigest()
                              if str(p) in note_texts else None for p in disposition_notes},
            "scope": "Selected notes and nonexcluded current text read; historical snapshots and explicitly unusable text excluded; original audio, raw outputs and speech accuracy not verified"}


def search(selection, terms, *, limit=100, context=2):
    """Literal, case-insensitive OR search; raw HTML notes remain visible."""
    if not terms or any(not isinstance(t, str) or not t.strip() for t in terms):
        raise ValueError("Supply nonempty search terms, including useful spelling variants")
    if limit < 1 or context < 0 or context > 20:
        raise ValueError("Use a positive result limit and 0–20 context lines")
    terms = [t.casefold() for t in terms]
    roles = {p: "descriptive_note" for p in selection["notes"]}
    roles.update({r["preferred_text"]: r["text_role"] for r in selection["recordings"] if r["preferred_text"]})
    matches = []
    total = 0
    for path, text in selection["texts"].items():
        lines = text.splitlines()
        for index, line in enumerate(lines):
            if not any(term in line.casefold() for term in terms):
                continue
            total += 1
            if len(matches) < limit:
                start, end = max(0, index - context), min(len(lines), index + context + 1)
                matches.append({"path": path, "role": roles[path], "line": index + 1,
                                "context_start_line": start + 1, "text": "\n".join(lines[start:end])})
    return {"matches": matches, "matching_lines": total, "truncated": total > limit,
            "searched_files": len(selection["texts"]),
            "note": "Raw Markdown includes annotations; notes and questions are not verified speech. A miss is limited to this selection and these terms."}


def reading_guide(rows, folder, manifest):
    """A compact reading table for a plain interview folder."""
    lines = [BEGIN, "## Recordings", "",
             "| Recording | Read | Original | Status |",
             "| --- | --- | --- | --- |"]
    for row in rows:
        label = Path(row["source"]).name.replace("|", "\\|")
        text_label = "Retained unusable text" if row["excluded_from_default_search"] else "Transcript"
        status = "**Unusable; excluded from search**" if row["excluded_from_default_search"] else "Machine text; not audio-verified"
        if not row["excluded_from_default_search"]:
            status += ("; text-inferred roles" if row.get("role_review", {}).get("complete") else "; role review pending")
        text = _link(text_label, row["preferred_text"], folder) if row["preferred_text"] else "Unavailable"
        lines.append(f"| {label} | {text} | {_link('Recording', row['source'], folder)} | {status} |")
    lines += ["", "Times refer to each original recording. Read and edit the linked transcript; "
              "keep uncertainty and annotations visible. Recording numbers follow the declared order.", END]
    return "\n".join(lines)


def guide(rows, folder, manifest):
    return reading_guide(rows, folder, manifest)


def _update_readme(folder, block, manifest, manifest_hash, fetch):
    folder = Path(folder)
    path, record = folder / "README.md", folder / ".reading-guide.json"
    for p in (path, record):
        if p.exists() and not _regular(p):
            raise ValueError(f"Guide target must be a regular file: {p}")
    if (folder / ".reading-history").is_symlink() or (folder / ".reading.lock").is_symlink():
        raise ValueError("Guide history and lock must not be symlinks")
    materialize([p for p in (path, record) if p.exists()], fetch)
    with lock(folder / ".reading.lock"):
        old = path.read_bytes().decode("utf-8") if path.exists() else f"# {folder.name}\n"
        validate_markers(old)
        if old.count(BEGIN) != old.count(END) or old.count(BEGIN) > 1:
            raise ValueError(f"Malformed guide markers: {path}")
        previous = json.loads(record.read_text()) if record.exists() else None
        match = re.search(re.escape(BEGIN) + r".*?" + re.escape(END), old, re.S)
        if BEGIN in old and match is None:
            raise ValueError(f"Reversed guide markers: {path}")
        old_hash = hashlib.sha256(old.encode()).hexdigest()
        pending_before = previous and previous.get("publication") == "prepared" and old_hash == previous.get("before_readme_sha256")
        if previous and not match and not pending_before:
            raise ValueError(f"Recorded guide was removed; reconcile human changes: {path}")
        if match:
            existing = match.group()
            digest = hashlib.sha256(existing.encode()).hexdigest()
            if existing != block and not pending_before and (not previous or digest != previous.get("block_sha256")):
                raise ValueError(f"Generated guide was edited; preserve and reconcile it: {path}")
            new = old[:match.start()] + block + old[match.end():]
        else:
            first, separator, rest = old.partition("\n")
            if first.startswith("# "):
                new = first + "\n\n" + block + "\n\n" + rest.lstrip("\n")
            else:
                new = block + "\n\n" + old
        backup = folder / ".reading-history" / (old_hash + ".md")
        snapshot = {"schema": 1, "block_sha256": hashlib.sha256(block.encode()).hexdigest(),
                    "manifest": os.path.relpath(manifest, folder), "manifest_sha256": manifest_hash,
                    "scope": "Generated navigation only; human prose and transcripts are not immutable outputs"}
        if previous and "initial_readme" in previous:
            snapshot["initial_readme"] = previous["initial_readme"]
        elif new != old:
            snapshot["initial_readme"] = os.path.relpath(backup, folder)
        if new != old:
            if backup.exists():
                if checksum(backup) != old_hash:
                    raise ValueError("Changed reading-history backup")
            else:
                atomic(backup, old, replace=False)
            atomic(record, json_bytes({**snapshot, "publication": "prepared", "before_readme_sha256": old_hash}))
            atomic(path, new)
        snapshot["publication"] = "complete"
        atomic(record, json_bytes(snapshot))
        return {"readme": str(path), "changed": old != new}


def update_guides(selection, *, fetch=False):
    results = []
    for manifest, digest in selection["manifest_hashes"].items():
        manifest = Path(manifest)
        rows = [r for r in selection["recordings"] if r["manifest"] == str(manifest)]
        if checksum(manifest) != digest:
            raise ValueError("Manifest changed while preparing reading guides")
        results.append(_update_readme(manifest.parent, guide(rows, manifest.parent, manifest), manifest, digest, fetch))
    return results


def process(args, config):
    sessions = args.session or ([config["active_session"]] if config.get("active_session") else [])
    selection = collect(sessions, fetch=args.fetch, notes=args.notes)
    output = {k: v for k, v in selection.items() if k != "texts"}
    if args.query:
        output["search"] = search(selection, args.query, limit=args.limit, context=args.context)
    if args.update_guides:
        output["guides"] = update_guides(selection, fetch=args.fetch)
    output["missing_preferred_text"] = sum(not r["preferred_text"] for r in selection["recordings"])
    output["excluded_unusable_text"] = sum(r["excluded_from_default_search"] for r in selection["recordings"])
    return output, 0

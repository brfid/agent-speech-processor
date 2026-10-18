"""Resolve an explicitly registered, user-owned working transcript.

A job sidecar points to a reading manifest; the manifest owns the location.
Historical drafts and receipts remain untouched. Text is never hash-gated.
"""
import json
from pathlib import Path

from .inputs import materialize

SIDECAR = ".working-transcript.json"


def _folder(value):
    supplied = Path(value).expanduser().absolute()
    if supplied.is_symlink() or not supplied.is_dir():
        raise ValueError(f"Working-transcript job must be an existing directory, not a symlink: {supplied}")
    return supplied.resolve()


def relative(base, value):
    """Resolve a portable relative locator without following symlink components."""
    if not isinstance(value, str) or not value or "\0" in value or Path(value).is_absolute():
        raise ValueError("Working-transcript locators must be nonempty relative paths")
    current = Path(base).resolve()
    for part in Path(value).parts:
        if part == "..":
            current = current.parent
        elif part != ".":
            current = current / part
        if current.is_symlink():
            raise ValueError(f"Working-transcript locator contains a symlink: {current}")
    return current


def _regular(path, label):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"{label} must be an existing regular file, not a symlink: {path}")


def _control(path, fetch):
    _regular(path, "Working-transcript control")
    if fetch:
        materialize([path], True)
        _regular(path, "Working-transcript control")
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"Working-transcript control must contain a JSON object: {path}")
    return data


def _mapping(manifest, folder, fetch):
    data = _control(manifest, fetch)
    rows = data.get("recordings")
    if data.get("schema") != 1 or data.get("reading_layout") != 1 or not isinstance(rows, list) or not rows:
        raise ValueError(f"Unsupported or empty canonical reading manifest: {manifest}")
    matches, mappings = [], []
    for item in rows:
        if not isinstance(item, dict):
            raise ValueError(f"Reading manifest recordings must be objects: {manifest}")
        draft = relative(manifest.parent, item.get("draft"))
        source = relative(manifest.parent, item.get("source"))
        transcript = relative(manifest.parent, item.get("working_transcript"))
        mappings.append((item, draft, source, transcript))
        if draft == folder:
            matches.append(item)
    if len(matches) != 1:
        raise ValueError(f"Reading manifest must map exactly one recording to this job: {folder}")
    item = matches[0]
    targets = [row[3] for row in mappings]
    if len(targets) != len(set(targets)):
        raise ValueError("Distinct manifest recordings cannot share one canonical working transcript")
    for _, _, original, target in mappings:
        if (target.parent != manifest.parent or target.suffix.lower() not in (".md", ".markdown")
                or target == original or target.name == "transcript-draft.md"
                or any(target.is_relative_to(draft) for _, draft, _, _ in mappings)):
            raise ValueError("Canonical working transcript must be separate Markdown directly beside its reading manifest, outside processing jobs")
    _, _, source, transcript = next(row for row in mappings if row[0] is item)
    return {"manifest": manifest, "transcript": transcript, "source": source, "folder": folder, "item": item}


def _available(result, *, fetch, expected_source, require):
    if expected_source is not None and Path(expected_source).expanduser().resolve() != result["source"]:
        raise ValueError("Canonical working transcript belongs to another original recording")
    path = result["transcript"]
    if path.is_symlink() or (path.exists() and not path.is_file()):
        raise ValueError(f"Canonical working transcript must be a regular file, not a symlink: {path}")
    result["available"] = path.is_file()
    if require:
        if not result["available"]:
            raise ValueError(f"Registered working transcript is missing; restore the user-owned file instead of regenerating it: {path}")
        if fetch:
            materialize([path], True)
            _regular(path, "Canonical working transcript")
        path.read_text(encoding="utf-8")  # Readability is not a seed-hash comparison.
    return result


def resolve(folder, *, fetch=False, expected_source=None, require=True):
    """Return the registered mapping, or None when this job has no sidecar.

    With require=False, text availability is metadata only. Controls are still
    checked, but original audio and machine receipts are never opened or fetched.
    Missing registered text is a recovery error when require=True.
    """
    folder = _folder(folder)
    sidecar = folder / SIDECAR
    if not sidecar.exists() and not sidecar.is_symlink():
        return None
    registration = _control(sidecar, fetch)
    if registration.get("schema") != 1:
        raise ValueError(f"Unsupported working-transcript registration: {sidecar}")
    manifest = relative(folder, registration.get("manifest"))
    return _available(_mapping(manifest, folder, fetch), fetch=fetch,
                      expected_source=expected_source, require=require)


def from_manifest(manifest, item, folder, *, fetch=False, expected_source=None, require=True):
    """Validate an explicit canonical mapping and its required job registration."""
    folder = _folder(folder)
    supplied = Path(manifest).expanduser().absolute()
    if supplied.is_symlink():
        raise ValueError(f"Reading manifest must not be a symlink: {supplied}")
    manifest = supplied.parent.resolve() / supplied.name
    result = _mapping(manifest, folder, fetch)
    if not isinstance(item, dict) or any(item.get(key) != result["item"].get(key)
                                         for key in ("source", "draft", "working_transcript")):
        raise ValueError("Explicit working-transcript mapping differs from the selected manifest recording")
    registered = resolve(folder, fetch=fetch, expected_source=expected_source, require=False)
    if registered is None:
        raise ValueError(f"Reading requires a registered canonical working transcript; promote the completed session: {folder}")
    if any(result[key] != registered[key] for key in ("manifest", "source", "transcript")):
        raise ValueError("Explicit working-transcript mapping conflicts with the job registration")
    return _available(result, fetch=fetch, expected_source=expected_source, require=require)

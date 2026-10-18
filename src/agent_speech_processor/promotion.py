"""Promote selected working transcripts without moving or rewriting historical jobs."""
from contextlib import ExitStack
import hashlib
import json
import os
from pathlib import Path
import re
from urllib.parse import quote, unquote, urlsplit

from .inputs import materialize
from .records import atomic, checksum, json_bytes
from .transcripts import header
from .role_text import PENDING, status as role_status, file_status, summary
from .markup import opaque_regions, protected_mask
from .publication import regular as _regular, lock as _lock, same_or_create as _same_or_create
from .reading_policy import file_disposition


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _inside(folder, name):
    relative = Path(name)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError("Publication path must stay inside the reading folder")
    path = folder / relative
    for candidate in [path, *path.parents]:
        if candidate == folder:
            break
        if candidate.is_symlink():
            raise ValueError(f"Publication path must not be a symlink: {candidate}")
    if not path.resolve().is_relative_to(folder.resolve()):
        raise ValueError("Publication path escapes the reading folder")
    return path


def _target(value, old_parent, new_parent):
    """Rebase a Markdown destination, retaining its query and fragment."""
    if not value or value.startswith(("#", "//")):
        return value
    decoded = re.sub(r"\\([!\"#$%&'()*+,\-./:;<=>?@\[\]^_`{|}~\\])", r"\1", value)
    parsed = urlsplit(decoded)
    if parsed.scheme:
        return value
    if decoded.startswith("~") or "\\" in decoded or any(c in decoded for c in "\r\n"):
        raise ValueError(f"Unsupported relative Markdown destination: {value}")
    if not parsed.path:
        return value
    original = (old_parent / unquote(parsed.path)).resolve()
    result = quote(os.path.relpath(original, new_parent), safe="/-._~")
    if parsed.query:
        result += "?" + parsed.query
    if parsed.fragment:
        result += "#" + parsed.fragment
    return result


def _destination(text, cursor, *, inline):
    while cursor < len(text) and text[cursor] in " \t":
        cursor += 1
    start = cursor
    if cursor < len(text) and text[cursor] == "<":
        start = cursor + 1
        end = text.find(">", start)
        if end < 0 or "\n" in text[start:end]:
            raise ValueError("Unclosed angle Markdown destination")
        return start, end, end + 1
    depth = 0
    while cursor < len(text):
        char = text[cursor]
        if char == "\\" and cursor + 1 < len(text):
            cursor += 2
            continue
        if char.isspace():
            break
        if char == "(":
            depth += 1
        elif char == ")":
            if depth == 0:
                break
            depth -= 1
        cursor += 1
    if depth:
        raise ValueError("Unbalanced Markdown destination")
    if cursor == start and not (inline and text[cursor:cursor + 1] == ")"):
        raise ValueError("Empty or ambiguous Markdown destination")
    return start, cursor, cursor


def _inline_tail(text, after):
    closing = re.match(r"[ \t]*(?:\"[^\"\n]*\"|'[^'\n]*'|\([^()\n]*\))?[ \t]*\)", text[after:])
    if not closing:
        raise ValueError("Ambiguous inline Markdown link; use an angle destination")
    return after + closing.end()


def rebase_markdown(data, old_parent, new_parent):
    """Change validated destination spans only; preserve all other UTF-8 bytes.

    Inline links/images, balanced parentheses, angle destinations and reference
    definitions are supported. Code and HTML comments are copied verbatim.
    Raw HTML links and ambiguous/malformed destinations stop the migration.
    """
    text = data.decode("utf-8")
    regions = opaque_regions(text)
    for start, end, kind in regions:
        if kind == "indented_code" and re.search(r"\]\(|\[[^\]\n]+\]:", text[start:end]):
            raise ValueError("Indented Markdown link context requires explicit relocation review")
    protected = protected_mask(text, regions)
    edits, occupied = [], set()
    for match in re.finditer(r"<(?:a|img|source)\b[^>]*\b(?:href|src)\s*=", text, re.I):
        if not protected[match.start()]:
            raise ValueError("Raw HTML link requires explicit relocation review")
    candidates = []
    brackets, index = [], 0
    block_breaks = {m.start() for m in re.finditer(r"\r?\n[ \t]*\r?\n", text)}
    while index < len(text):
        char = text[index]
        if index in block_breaks:
            brackets.clear()
        if protected[index]:
            index += 1
            continue
        backslashes, previous = 0, index - 1
        while previous >= 0 and text[previous] == "\\":
            backslashes += 1
            previous -= 1
        if backslashes % 2:
            index += 1
            continue
        if char == "[":
            brackets.append(index)
        elif char == "]" and brackets:
            brackets.pop()
            if text[index + 1:index + 2] == "(":
                candidates.append((index + 2, True))
                _, _, after = _destination(text, index + 2, inline=True)
                index = _inline_tail(text, after)
                continue
        index += 1
    for match in re.finditer(r"(?m)^ {0,3}\[[^\]\n]+\]:[ \t]*", text):
        if not protected[match.start()]:
            candidates.append((match.end(), False))
    for cursor, inline in sorted(candidates):
        if cursor in occupied:
            continue
        start, end, after = _destination(text, cursor, inline=inline)
        tail = text[after:]
        if inline:
            # A title may follow the destination. No arbitrary text may be lost.
            occupied.update(range(cursor, _inline_tail(text, after)))
        else:
            line = tail.split("\n", 1)[0]
            if not re.fullmatch(r"[ \t]*(?:\"[^\"]*\"|'[^']*'|\([^()]*\))?[ \t\r]*", line):
                raise ValueError("Ambiguous reference Markdown destination")
        before = text[start:end]
        after_value = _target(before, Path(old_parent), Path(new_parent))
        if before != after_value:
            edits.append({"start": len(text[:start].encode()), "end": len(text[:end].encode()),
                          "before": before, "after": after_value})
    output = data
    for edit in reversed(edits):
        output = output[:edit["start"]] + edit["after"].encode() + output[edit["end"]:]
    return output, edits


def _link(label, target, folder):
    label = str(label).replace("[", "\\[").replace("]", "\\]").replace("|", "\\|")
    return f"[{label}](<{quote(os.path.relpath(target, folder), safe='/')}>)"


def _render(before, old_file, source, streams, output, unusable):
    text = before.decode("utf-8")
    body = re.search(r"(?m)^## Source window ", text)
    duration = re.search(r"(?m)^Recording: .* Duration: ([0-9.]+) seconds\.$", text)
    baseline = re.search(r"(?m)^Baseline: .* SHA-256 `([a-f0-9]{64})`\.$", text)
    if not body or not duration or not baseline:
        raise ValueError(f"Unsupported transcript preface; preserve it for inspection: {old_file}")
    current = header(source, old_file.parent, float(duration[1]), streams,
                     edited=True, baseline=baseline[1])
    generated = current + PENDING
    if not text.startswith(generated) or len(generated) > body.start():
        raise ValueError(f"Modified or unsupported transcript preface: {old_file}")
    template = {"format": "edited-transcript-v1", "command_literal": "agent-speech-processor",
                "overlap_description": "anchored-case-only", "role_review_pending_seed": True}
    # Validate the current labels/notes before publishing a canonical copy.
    from .role_text import parse
    parse(text)
    # Any author-added preface follows the known boilerplate and remains visible.
    preserved = before[len(generated.encode()):]
    rebased, edits = rebase_markdown(preserved, old_file.parent, output)
    clean = "# Transcript\n\n"
    clean += ("**Retained unusable text; excluded from default search.**\n\n" if unusable else "")
    clean += ("Working machine transcript; not checked against audio. Annotations are not speech.\n\n"
              + (PENDING if not role_status(text)["complete"] else "")
              + "Original: " + _link(source.name, source, output) + ".\n\n"
              + "Times are approximate positions in this recording, starting at zero.\n\n")
    if body.start() > len(generated):
        clean += "## Supplied notes\n\n"
    body_offset = len(text[:body.start()].encode()) - len(generated.encode())
    body_edits = [e for e in edits if e["start"] >= body_offset]
    body_after = preserved[body_offset:]
    for edit in reversed(body_edits):
        a, b = edit["start"] - body_offset, edit["end"] - body_offset
        body_after = body_after[:a] + edit["after"].encode() + body_after[b:]
    return clean.encode() + rebased, {
        "recognized_preface": template,
        "before_body_sha256": _sha(preserved[body_offset:]), "after_body_sha256": _sha(body_after),
        "link_rewrites": [{**e, "start": e["start"] + len(generated.encode()),
                           "end": e["end"] + len(generated.encode())} for e in edits]}


def _load(session, output):
    data = json.loads(session.read_bytes())
    if data.get("schema") != 1 or data.get("reading_layout") or not isinstance(data.get("recordings"), list) or not data["recordings"]:
        raise ValueError("Promote an existing schema-1 historical session manifest")
    recordings, sources, folders = [], set(), set()
    for item in data["recordings"]:
        source = (session.parent / item["source"]).resolve()
        supplied = session.parent / item["draft"]
        if supplied.is_symlink() or not supplied.is_dir():
            raise ValueError("Historical job must be an existing nonsymlink folder")
        folder = supplied.resolve()
        if source in sources or folder in folders:
            raise ValueError("Select one current job per source, with distinct job folders")
        if output == folder or output.is_relative_to(folder) or source.is_relative_to(output):
            raise ValueError("Reading folder must be separate from jobs and original media")
        for field in ("source_sha256", "draft_recipe_sha256"):
            if not re.fullmatch(r"[a-f0-9]{64}", item.get(field, "")):
                raise ValueError(f"Missing or invalid {field}")
        streams = item.get("audio_streams")
        if not isinstance(streams, list) or not streams or any(type(i) is not int or i < 0 for i in streams) or len(set(streams)) != len(streams):
            raise ValueError("Select distinct nonnegative audio streams")
        sources.add(source)
        folders.add(folder)
        recordings.append((item, source, folder))
    return data, recordings


def _controls(output, state, *, allow_edits):
    for name, expected in state["outputs"].items():
        path = _inside(output, name)
        _regular(path)
        if (not allow_edits or name == ".session.json") and checksum(path) != expected:
            raise ValueError(f"Published file changed before handoff: {path}")


def _evidence_paths(output, state):
    return {**{_inside(output, name): expected for name, expected in state["evidence"].items()},
            **{_inside(output, ".promotion/prepared/" + name): expected for name, expected in state["outputs"].items()}}


def _register(output, folders):
    for folder in folders:
        sidecar = folder / ".working-transcript.json"
        data = json_bytes({"schema": 1, "manifest": os.path.relpath(output / ".session.json", folder)})
        _same_or_create(sidecar, data)


def _result(output, state):
    reports = [{"transcript": row["working_transcript"],
                **file_status(_inside(output, row["working_transcript"]))}
               for row in state["recordings"]
               if file_disposition(output / row["draft"] / "README.md") != "unusable"]
    return {"output": str(output), "manifest": str(output / ".session.json"),
            "recordings": len(state["recordings"]), "publication": "complete",
            "role_review": summary(reports),
            "next_step": "Use agent-speech-processor roles --session <manifest> to complete/check contextual role review"}


def promote(session, output, *, readme_text=None, fetch=False):
    """Publish a canonical reading folder; never read audio or run recognition.

    Only README.md and Transcript*.md are visible. Historical jobs/working text
    remain untouched; their sidecars redirect supported tools to the new files.
    Retrying an incomplete publication requires unchanged inputs and README
    request. After handoff, user-owned text/README are never hash-gated or reset.
    """
    session = Path(session).expanduser().absolute()
    _regular(session)
    session = session.resolve()
    output = Path(output).expanduser().absolute()
    if output.is_symlink():
        raise ValueError("Reading folder must not be a symlink")
    output = output.resolve()
    output.mkdir(parents=True, exist_ok=True)
    ledger = _inside(output, ".promotion.json")
    intake = []

    def take(paths):
        for path in paths:
            _regular(path)
        receipt = materialize(paths, fetch)
        if receipt is not None:
            intake.append(receipt)

    with _lock(_inside(output, ".promotion.lock")):
        take([session] + ([ledger] if ledger.exists() else []))
        manifest_bytes = session.read_bytes()
        manifest_sha = _sha(manifest_bytes)
        state = json.loads(ledger.read_bytes()) if ledger.exists() else None
        requested = _sha(readme_text.encode()) if readme_text is not None else None
        if state:
            if state.get("schema") != 1 or state.get("session") != os.path.relpath(session, output) or state.get("session_sha256") != manifest_sha:
                raise ValueError("Promotion session identity changed; preserve the existing publication")
            if state["publication"] not in ("published", "complete") and state["readme_request_sha256"] != requested:
                raise ValueError("README request changed during interrupted publication")
        elif any(p.name not in {".promotion.lock"} for p in output.iterdir()):
            raise ValueError("New promotion requires an empty reading folder")
        original, recordings = _load(session, output)
        with ExitStack() as locks:
            for folder in sorted((r[2] for r in recordings), key=str):
                locks.enter_context(_lock(folder / ".processing.lock"))
            sidecars = [folder / ".working-transcript.json" for _, _, folder in recordings]
            take([p for p in sidecars if p.exists() or p.is_symlink()])
            for sidecar in sidecars:
                if sidecar.exists():
                    expected = json_bytes({"schema": 1, "manifest": os.path.relpath(output / ".session.json", sidecar.parent)})
                    if sidecar.read_bytes() != expected:
                        raise ValueError("A selected job already has a different canonical working transcript")
            if state and state["publication"] in ("published", "complete"):
                evidence = _evidence_paths(output, state)
                take([_inside(output, name) for name in state["outputs"]] + list(evidence))
                take([folder / "README.md" for _, _, folder in recordings
                      if (folder / "README.md").exists() or (folder / "README.md").is_symlink()])
                _controls(output, state, allow_edits=True)
                for path, expected in evidence.items():
                    if checksum(path) != expected:
                        raise ValueError(f"Historical promotion evidence changed: {path}")
                _register(output, [r[2] for r in recordings])
                state["publication"] = "complete"
                state.setdefault("intake", []).extend(intake)
                atomic(ledger, json_bytes(state))
                return _result(output, state)
            controls = [folder / "transcript-edited.md" for _, _, folder in recordings]
            notes = [folder / "README.md" for _, _, folder in recordings if (folder / "README.md").exists()]
            if (session.parent / "README.md").exists():
                notes.append(session.parent / "README.md")
            take(controls + notes)
            files = {".promotion/original-session.json": manifest_bytes}
            for number, note in enumerate(notes, 1):
                files[f".promotion/notes-{number:02}.md"] = note.read_bytes()
            manifest = dict(original, reading_layout=1, recordings=[])
            rows, lines, inputs = [], [], {os.path.relpath(session, output): manifest_sha}
            for note in notes:
                inputs[os.path.relpath(note, output)] = checksum(note)
            for number, (item, source, folder) in enumerate(recordings, 1):
                old = folder / "transcript-edited.md"
                before = old.read_bytes()
                note = folder / "README.md"
                unusable = file_disposition(note) == "unusable"
                name = "Transcript.md" if len(recordings) == 1 else f"Transcript - {number:02}.md"
                if unusable:
                    name = name[:-3] + " - Unusable.md"
                after, evidence = _render(before, old, source, item["audio_streams"], output, unusable)
                files[name], files[f".promotion/before-{number:02}.md"] = after, before
                copied = dict(item, source=os.path.relpath(source, output), draft=os.path.relpath(folder, output),
                              working_transcript=name)
                manifest["recordings"].append(copied)
                inputs[os.path.relpath(old, output)] = _sha(before)
                rows.append({"draft": copied["draft"], "historical_working_transcript": os.path.relpath(old, output),
                             "working_transcript": name, "before_sha256": _sha(before), "prepared_sha256": _sha(after),
                             "excluded_from_default_search": unusable, **evidence})
                lines.append(f"- {_link(name[:-3], output / name, output)} — {_link(source.name, source, output)}"
                             + (" (retained unusable text; excluded from default search)" if unusable else ""))
            if readme_text is None:
                readme_text = (f"# {output.name}\n\nWorking transcripts; speech and speaker identities remain unverified.\n\n"
                               + "Use `agent-speech-processor roles --session .session.json --check` for contextual role readiness; "
                               "complete the review packet before editorial handoff.\n\n"
                               + "\n".join(lines) + "\n\n")
                if (session.parent / "README.md").exists():
                    readme_text += _link("Historical session notes", session.parent / "README.md", output) + ".\n\n"
                readme_text += "Processing history: " + "; ".join(_link(str(i), f / "README.md" if (f / "README.md").exists() else f, output)
                                                                 for i, (_, _, f) in enumerate(recordings, 1)) + ".\n"
            files["README.md"], files[".session.json"] = readme_text.encode(), json_bytes(manifest)
            publication = {name: _sha(data) for name, data in files.items() if not name.startswith(".promotion/")}
            candidate = {"schema": 1, "session": os.path.relpath(session, output), "session_sha256": manifest_sha,
                         "readme_request_sha256": requested, "publication": "preparing", "inputs": inputs,
                         "outputs": publication, "evidence": {name: _sha(data) for name, data in files.items() if name.startswith(".promotion/")},
                         "recordings": rows, "intake": intake}
            if state:
                if any(state[key] != candidate[key] for key in ("inputs", "outputs", "evidence", "recordings")):
                    raise ValueError("Inputs or prepared publication changed during interrupted promotion")
                candidate["intake"] = state.get("intake", []) + intake
            state = candidate
            atomic(ledger, json_bytes(state))
            for name, data in files.items():
                staged = name if name.startswith(".promotion/") else ".promotion/prepared/" + name
                _same_or_create(_inside(output, staged), data)
            state["publication"] = "prepared"
            atomic(ledger, json_bytes(state))
            # Verify selected originals of the promotion (text/notes only) again
            # immediately before publication, while processing jobs remain locked.
            for reference, expected in inputs.items():
                if checksum(_regular(output / reference)) != expected:
                    raise ValueError("Historical text or notes changed during promotion")
            for name in publication:
                _same_or_create(_inside(output, name), files[name])
            _controls(output, state, allow_edits=False)
            for reference, expected in inputs.items():
                if checksum(_regular(output / reference)) != expected:
                    raise ValueError("Historical text or notes changed during publication")
            state["publication"] = "published"
            atomic(ledger, json_bytes(state))
            _register(output, [r[2] for r in recordings])
            state["publication"] = "complete"
            atomic(ledger, json_bytes(state))
            return _result(output, state)

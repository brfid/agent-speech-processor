"""Role-text parsing, readiness and exact label-only rendering."""
from pathlib import Path
import re

from .markup import protected_mask

LABELS = ("Interviewer", "Interviewee", "Unclear speakers")
NOTE = ("Roles are inferred from text, not checked against audio. **Interviewer** and **Interviewee** "
        "are provisional roles, not verified identities. **Unclear speakers** marks uncertain attribution "
        "or a block that may contain more than one role. Existing blocks and timestamps are retained; "
        "labels do not establish exact speaker turns.\n\n")
PENDING = ("Role review pending: these blocks have not received contextual speaker-role review. "
           "**Unclear speakers** is the initial placeholder. Complete `agent-speech-processor roles` before editorial handoff; "
           "wording, timestamps and annotations must remain unchanged.\n\n")
BLOCK = re.compile(r"(?m)^(\[\d{2}:\d{2}:\d{2}\.\d{3}–\d{2}:\d{2}:\d{2}\.\d{3}\] )"
                   r"\*\*(Interviewer|Interviewee|Unclear speakers):\*\* ([^\r\n]+)(?=\r?$)")
_NOTE_PATTERNS = {
    "qualified": [re.compile(r"[ \t\r\n]+".join(re.escape(word) for word in NOTE.split())
                              + r"(?:[ \t]*\r?\n){2}")],
    "pending": [re.compile(r"[ \t\r\n]+".join(re.escape(word) for word in note.split())
                            + r"(?:[ \t]*\r?\n){2}")
                for note in (PENDING,)],
}


def parse(text):
    protected = protected_mask(text)
    matches = [m for m in BLOCK.finditer(text) if not protected[m.start()]]
    timed = [m for m in re.finditer(r"(?m)^\[\d{2}:\d{2}:\d{2}", text) if not protected[m.start()]]
    if not matches or len(matches) != len(timed):
        raise ValueError("Role review requires every timed block in the supported transcript format")
    notes = {}
    for name, patterns in _NOTE_PATTERNS.items():
        positions = [(m.start(), m.end()) for pattern in patterns
                     for m in pattern.finditer(text[:matches[0].start()])
                     if not protected[m.start()]]
        if len(positions) > 1:
            raise ValueError("Duplicate role-review explanatory notes require inspection")
        notes[name] = positions[0] if positions else None
    for match in re.finditer(r"(?m)^Role review pending:", text[:matches[0].start()]):
        if not protected[match.start()] and not (notes["pending"] and notes["pending"][0] <= match.start() < notes["pending"][1]):
            raise ValueError("Unsupported role-review explanatory note requires inspection")
    return matches, notes


def blocks(text):
    return parse(text)[0]


def status(text):
    try:
        matches, notes = parse(text)
    except ValueError as exc:
        return {"complete": False, "status": "pending", "reason": str(exc)}
    counts = {label: sum(m[2] == label for m in matches) for label in LABELS}
    unassigned = sum(m[2] not in LABELS for m in matches)
    complete = not unassigned and bool(notes["qualified"]) and not notes["pending"]
    return {"complete": complete, "status": "text_inferred" if complete else "pending",
            "timed_blocks": len(matches), "counts": counts, "unassigned_blocks": unassigned,
            "scope": "Text-inferred roles and explanatory note; not verified voices, exact turns or audio review"}


def file_status(path):
    try:
        path = Path(path)
        if path.is_symlink() or not path.is_file():
            raise ValueError("Current working transcript is unavailable")
        return status(path.read_bytes().decode("utf-8"))
    except (OSError, ValueError) as exc:
        return {"complete": False, "status": "pending", "reason": str(exc)}


def summary(reports):
    return {"complete": all(r["complete"] for r in reports), "recordings": reports,
            "scope": "Editorial role readiness only; machine/source verification is separate"}


def _speech(text):
    matches, notes = parse(text)
    changes = [(m.start(2), m.end(2), "ROLE") for m in matches]
    changes += [(span[0], span[1], "") for span in notes.values() if span]
    parts, cursor = [], 0
    for start, end, replacement in sorted(changes):
        parts.extend((text[cursor:start], replacement))
        cursor = end
    return "".join(parts) + text[cursor:]


def render(before, decisions):
    text = before.decode("utf-8")
    matches, _ = parse(text)
    if not isinstance(decisions, list) or len(decisions) != len(matches):
        raise ValueError("Decisions must cover every timed block exactly once")
    parts, cursor = [], 0
    for i, (m, decision) in enumerate(zip(matches, decisions), 1):
        if (not isinstance(decision, dict) or type(decision.get("block")) is not int
                or decision["block"] != i or decision.get("span") != m[1].strip()
                or decision.get("text") != m[3] or decision.get("role") not in LABELS):
            raise ValueError(f"Invalid, incomplete or changed role decision at block {i}")
        parts.extend((text[cursor:m.start(2)], decision["role"]))
        cursor = m.end(2)
    after = "".join(parts) + text[cursor:]
    _, notes = parse(after)
    if notes["pending"]:
        start, end = notes["pending"]
        after = after[:start] + after[end:]
    if not notes["qualified"]:
        heading = re.match(r"^# [^\r\n]+(?:\r?\n){2}", after)
        if not heading:
            raise ValueError("Unsupported transcript preface")
        newline = "\r\n" if "\r\n" in text else "\n"
        after = after[:heading.end()] + NOTE.replace("\n", newline) + after[heading.end():]
    if _speech(after) != _speech(text):
        raise ValueError("Role application changed wording, times, blocks or annotations")
    return after.encode()

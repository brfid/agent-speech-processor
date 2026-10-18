"""Read-only source/output integrity reports, separate from speech quality."""
import json
from pathlib import Path

from .inputs import materialize
from .job import Job, validate_audio_receipt


def recorded_job(folder, state):
    """Apply validated audio-retention rules at the draft adapter boundary."""
    from .retention import disposed_outputs
    audio = state["specification"]["kind"] == "audio"
    if audio:
        validate_audio_receipt(state)
    missing = disposed_outputs(folder, state) if audio else {}
    return Job(folder, state, missing_outputs=missing)


def process(args):
    folder = Path(args.output).expanduser().resolve()
    materialize([folder / ".processing.json"], args.fetch)
    state = json.loads((folder / ".processing.json").read_text())
    current = recorded_job(folder, state)
    materialize(current.source_paths() + [folder / ".processing.json"], args.fetch)
    paths = current.required_output_paths()
    materialize(paths, args.fetch)
    from .drafts import draft_completion
    errors, incomplete = [], []
    try:
        current.verify_inputs_unchanged()
    except (OSError, ValueError, RuntimeError) as exc:
        errors.append(str(exc))
    for name, step in state["steps"].items():
        if step["status"] != "done":
            incomplete.append(name)
        for path, digest in step.get("outputs", {}).items():
            try:
                current.verify_output(path, digest)
            except (OSError, ValueError) as exc:
                errors.append(str(exc))
    return {"verified": not errors and not incomplete, "byte_integrity": not errors,
            "integrity_errors": errors, "incomplete_steps": incomplete, "outputs": len(paths),
            "draft_completion": draft_completion(current),
            "edited_transcript": "user-owned; excluded from machine-output hashes",
            "scope": "Recorded source and machine-output byte integrity; draft completion is separate, not speech accuracy or audio review"}, 1 if errors else (2 if incomplete else 0)

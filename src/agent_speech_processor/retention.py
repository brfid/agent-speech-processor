"""Explicit, verified disposal of reproducible draft WAV caches only."""
import fcntl
import json
from pathlib import Path
import tempfile

from .records import atomic, checksum, json_bytes, now, digest
from .processes import run
from .inputs import materialize
from .audio import audio_input_command, check_space

RECORD = "audio-retention.json"
HISTORY = ".processing.before-audio-prune.json"


def disposed_outputs(folder, state):
    path = Path(folder) / RECORD
    if not path.exists() and not path.is_symlink():
        return {}
    if path.is_symlink():
        raise ValueError("Audio retention record must not be a symlink")
    record = json.loads(path.read_text())
    expected_sources = sorted(x["sha256"] for x in state["sources"].values())
    if (record.get("schema") != 1 or record.get("source_sha256") != expected_sources
            or record.get("settings_sha256") != digest(state["specification"]["settings"])):
        raise ValueError("Audio retention record belongs to another source or recipe")
    history = Path(folder) / HISTORY
    if history.is_symlink() or checksum(history) != record["historical_receipt_sha256"]:
        raise ValueError("Preserved pre-cleanup receipt is missing or modified")
    expected = {name: value for step in state["steps"].values() for name, value in step.get("outputs", {}).items()}
    removed = record.get("disposable", {})
    if not isinstance(removed, dict):
        raise ValueError("Invalid disposable audio mapping")
    for relative, value in removed.items():
        part = Path(relative)
        if (part.is_absolute() or ".." in part.parts or len(part.parts) != 2
                or part.parts[0] != "chunks" or part.suffix != ".wav"
                or expected.get(relative) != value.get("sha256")
                or value.get("recreated_sha256") != value.get("sha256")):
            raise ValueError("Retention exception must name an exactly recreated, recorded chunk WAV")
    return {relative: value["sha256"] for relative, value in removed.items()}


def extraction_command(source, start, end, indices, wav, adapter):
    if adapter != 4:
        raise ValueError("Audio recreation requires the current draft adapter 4")
    return audio_input_command(source, start, end, indices, wav)


def process(args):
    from .verification import recorded_job
    folder = Path(args.output).expanduser().resolve()
    ledger = folder / ".processing.json"
    materialize([ledger], args.fetch)
    state = json.loads(ledger.read_text())
    from .job import validate_audio_receipt
    validate_audio_receipt(state)
    if any(step.get("status") != "done" for step in state["steps"].values()):
        raise ValueError("Finish or investigate incomplete steps before pruning audio")
    current = recorded_job(folder, state)
    prior = disposed_outputs(folder, state)
    candidates = []
    for step in state["steps"].values():
        detail = step.get("details", {})
        if "json" not in detail:
            continue
        relative = str(Path(detail["json"]).with_suffix(".wav"))
        if relative not in step["outputs"]:
            continue
        path = current.output_path(relative)
        if path.is_symlink():
            raise ValueError("Refusing to prune a symlink")
        if relative in prior and not path.exists():
            continue
        if not path.is_file():
            raise ValueError("A required WAV is already missing without a retention record")
        if Path(relative).parts[0] != "chunks" or len(Path(relative).parts) != 2:
            raise ValueError("Refusing to prune anything outside recorded chunk WAVs")
        candidates.append({"path": relative, "bytes": path.stat().st_size,
                           "sha256": step["outputs"][relative], "span": detail})
    result = {"mode": "apply" if args.apply else "preview", "output": str(folder),
              "files": [{k: v for k, v in item.items() if k != "span"} for item in candidates],
              "bytes": sum(item["bytes"] for item in candidates), "already_disposable": len(prior)}
    if not args.apply or not candidates:
        return result
    lock = folder / ".processing.lock"
    if lock.is_symlink():
        raise ValueError("Processing lock must not be a symlink")
    with lock.open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("A writer is active; audio pruning stopped") from exc
        original_receipt = ledger.read_bytes()
        if json.loads(original_receipt) != state:
            raise ValueError("Processing state changed after preview")
        materialize(current.source_paths() + current.required_output_paths(), args.fetch)
        current.verify_inputs_unchanged()
        for step in state["steps"].values():
            for relative, expected in step["outputs"].items():
                current.verify_output(relative, expected)
        if len(current.source_paths()) != 1:
            raise ValueError("Audio pruning requires exactly one original")
        source = current.source_paths()[0]
        settings = state["specification"]["settings"]
        indices = [int(x.removeprefix("0:a:")) for x in settings["audio_stream"].split(",")]
        recreated = {}
        for item in candidates:
            span = item["span"]
            check_space(folder, span["end_seconds"] - span["start_seconds"])
            with tempfile.TemporaryDirectory(prefix="interview-recreate-") as temporary:
                wav = Path(temporary) / "audio.wav"
                command = extraction_command(source, span["start_seconds"], span["end_seconds"], indices,
                                             wav, settings["adapter_version"])
                run(command, timeout=300)
                reproduced = checksum(wav)
                if reproduced != item["sha256"]:
                    raise ValueError("Recreated WAV differs; nothing pruned: " + item["path"])
                recreated[item["path"]] = {"sha256": item["sha256"], "recreated_sha256": reproduced,
                    "bytes": item["bytes"], "extract_argv": ["{source}" if str(x) == str(source)
                        else "{wav}" if str(x) == str(wav) else str(x) for x in command]}
        current.verify_inputs_unchanged()
        if ledger.read_bytes() != original_receipt:
            raise ValueError("Processing receipt changed; nothing pruned")
        history = folder / HISTORY
        if history.is_symlink():
            raise ValueError("Historical receipt must not be a symlink")
        if not prior and history.exists() and history.read_bytes() != original_receipt:
            raise ValueError("An unrelated historical receipt already exists; nothing pruned")
        if not history.exists():
            atomic(history, original_receipt, replace=False)
        record_path = folder / RECORD
        if prior:
            record = json.loads(record_path.read_text())
        else:
            record = {"schema": 1, "created_at": now(), "source_sha256": sorted(x["sha256"] for x in state["sources"].values()),
                      "settings_sha256": digest(settings), "historical_receipt_sha256": checksum(history),
                      "disposable": {}, "method": "Every listed WAV was recreated byte-for-byte from its verified original before disposal."}
        record["disposable"].update(recreated)
        # Record intent before unlink: interruption leaves a valid mixture of
        # present (still hash-checked) and explicitly disposable absent WAVs.
        atomic(record_path, json_bytes(record))
        for item in candidates:
            path = current.output_path(item["path"])
            if path.is_symlink() or checksum(path) != item["sha256"]:
                raise ValueError("WAV changed before disposal; stopped: " + item["path"])
            path.unlink()
        result["removed_files"] = len(candidates)
        result["note"] = "Logical bytes removed; filesystem snapshots may delay free-space recovery."
    return result

"""Generic exact-input jobs, checked outputs, durable checkpoints and locks."""
import contextlib
import fcntl
import json
import os
import shutil
from pathlib import Path
import tempfile

from .inputs import fetch, prepare_sources
from .records import atomic, checksum, json_bytes, now


def validate_audio_receipt(state):
    """Accept the current recording-relative Whisper receipt only."""
    specification = state.get("specification", {})
    settings = specification.get("settings", {})
    if (state.get("schema") != 2 or specification.get("kind") != "audio"
            or settings.get("adapter_version") != 4 or settings.get("recipe_version") != 2):
        raise ValueError("Unsupported Whisper draft receipt; current drafts require schema 2, adapter 4 and recipe 2")


@contextlib.contextmanager
def job(output, kind, sources, settings, materialize, *, relative_sources=False, missing_outputs=None):
    """State is a mechanical receipt, never a document catalog or metadata authority."""
    sources = prepare_sources(sources)
    if not materialize and any("com~apple~CloudDocs" in source.parts for source in sources):
        raise ValueError("Use --fetch for iCloud sources so the installed icmat verifies the exact inputs")
    output = Path(output).expanduser().absolute()
    if output.exists() and output.is_symlink():
        raise ValueError("Derivative directory must not be a symlink")
    output = output.resolve()
    for source in sources:
        if source.resolve().is_relative_to(output.resolve()):
            raise ValueError("The derivative directory must not contain an original source")
    output.mkdir(parents=True, exist_ok=True)
    for name in (".processing.lock", ".processing.json", ".processing.previous.json", "icmat-receipt.json"):
        if (output / name).is_symlink():
            raise ValueError("Bookkeeping files must not be symlinks")
    with (output / ".processing.lock").open("a") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError("This output folder already has an active writer") from exc
        ledger = output / ".processing.json"
        source_keys = [os.path.relpath(p, output) if relative_sources else str(p) for p in sources]
        schema = 2 if relative_sources else 1
        specification = {"kind": kind, "sources": source_keys, "settings": settings}
        if ledger.exists():
            state = json.loads(ledger.read_text())
            if state.get("schema") != schema or state["specification"] != specification:
                raise ValueError("Inputs/settings differ from this job. Use a new output folder.")
            atomic(output / ".processing.previous.json", ledger.read_bytes())
        else:
            if any(p.name not in {"README.md", ".processing.lock"} for p in output.iterdir()):
                raise ValueError("New jobs require a fresh output folder (an existing README is allowed)")
            state = {"schema": schema, "specification": specification, "started_at": now(),
                     "sources": {}, "steps": {}}
        missing = missing_outputs(output, state) if missing_outputs else {}
        current = Job(output, state, missing_outputs=missing)
        current.save()
        if materialize:
            receipt, ok = fetch(sources)
            atomic(output / "icmat-receipt.json", json_bytes(receipt))
            if not ok:
                raise RuntimeError("icmat did not verify every input; receipt saved, processing stopped")
        for source, key in zip(sources, source_keys):
            before = source.stat()
            if getattr(before, "st_flags", 0) & 0x40000000:
                raise RuntimeError("Dataless source: rerun with --fetch: " + str(source))
            value = checksum(source)
            after = source.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError("Source changed during verification: " + str(source))
            previous = state["sources"].get(key)
            if previous and previous["sha256"] != value:
                raise ValueError("Source bytes changed. Preserve this job and use a new output folder.")
            state["sources"][key] = {"bytes": after.st_size, "sha256": value,
                                                  "verified_at": now()}
            current.save()
        try:
            yield current
            current.verify_inputs_unchanged()
        finally:
            current.save()


class Job:
    def __init__(self, folder, state, *, missing_outputs=None):
        if state.get("specification", {}).get("kind") == "audio":
            validate_audio_receipt(state)
        self.folder, self.state = Path(folder).resolve(), state
        # Only an adapter can supply validated exceptions for disposable outputs.
        self.missing_outputs = dict(missing_outputs or {})

    def save(self):
        atomic(self.folder / ".processing.json", json_bytes(self.state))

    def completed(self, name):
        return self.state["steps"].get(name, {}).get("status") == "done"

    def source_checksum(self, source):
        source = Path(source).resolve()
        key = os.path.relpath(source, self.folder) if self.state.get("schema") == 2 else str(source)
        return self.state["sources"][key]["sha256"]

    def source_paths(self):
        if self.state.get("schema") not in (1, 2):
            raise ValueError("Unsupported processing receipt schema")
        return [(self.folder / key).resolve() if self.state["schema"] == 2 else Path(key)
                for key in self.state["sources"]]

    def verify_output(self, relative, expected):
        path = self.output_path(relative)
        if not path.exists() and not path.is_symlink():
            if self.missing_outputs.get(relative) == expected:
                return False
        if path.is_symlink() or not path.is_file() or checksum(path) != expected:
            raise ValueError("A completed output is missing or modified: " + str(path))
        return True

    def required_output_paths(self):
        paths = []
        for step in self.state["steps"].values():
            for relative, expected in step.get("outputs", {}).items():
                path = self.output_path(relative)
                if self.missing_outputs.get(relative) == expected and not path.exists() and not path.is_symlink():
                    continue
                paths.append(path)
        return paths

    def output_path(self, relative):
        part = Path(relative)
        target = self.folder / part
        if part.is_absolute() or ".." in part.parts or not target.resolve().is_relative_to(self.folder.resolve()):
            raise ValueError("Output path escapes the derivative folder")
        return target

    def step(self, name, build, *, validate=None):
        """Checkpoint before publication, optionally validating a durable response.

        With validation, build receives (temporary_audio, durable_stage); it must
        save raw output there before returning. Validation receives staged files
        and details and must not call the recognizer. Rejected attempts stay in
        their original stages; interrupted preparation/publication is reusable.
        """
        old = self.state["steps"].get(name)
        if old and old["status"] == "done":
            for relative, expected in old["outputs"].items():
                self.verify_output(relative, expected)
            return old["details"]
        previous_outputs = (old or {}).get("outputs", {})
        entry = {"status": "running", "attempts": (old or {}).get("attempts", 0) + 1,
                 "started_at": now(), "outputs": dict(previous_outputs)}
        history = list((old or {}).get("failed_attempts", []))
        if old and old.get("prepared") and not old.get("rejected"):
            entry.update(prepared=old["prepared"], staging=old["staging"], details=old["details"])
        elif old:
            history.append({key: value for key, value in old.items() if key != "failed_attempts"})
        if history:
            entry["failed_attempts"] = history
        self.state["steps"][name] = entry
        self.save()
        try:
            if not entry.get("prepared"):
                # Keep a prepared model response across interruption. A model call need
                # not be byte-deterministic, and must not be repeated just to republish it.
                stage = Path(tempfile.mkdtemp(prefix=".stage-", dir=self.folder))
                entry["staging"] = stage.name
                self.save()
                with tempfile.TemporaryDirectory(prefix="interview-job-") as directory:
                    files, details = (build(Path(directory), stage) if validate
                                      else build(Path(directory)))
                    prepared = {}
                    for index, (relative, temporary) in enumerate(files.items()):
                        self.output_path(relative)
                        cached = stage / str(index)
                        shutil.copyfile(temporary, cached)
                        prepared[relative] = {"file": cached.name, "sha256": checksum(cached)}
                entry.update(prepared=prepared, details=details)
                self.save()
            stage = self.output_path(entry["staging"])
            if not stage.name.startswith(".stage-") or stage.is_symlink():
                raise ValueError("Invalid job staging directory")
            files = {}
            for relative, cached in entry["prepared"].items():
                self.output_path(relative)
                temporary = stage / cached["file"]
                expected = cached["sha256"]
                if temporary.parent != stage or temporary.is_symlink() or checksum(temporary) != expected:
                    raise ValueError("Prepared output changed before publication")
                files[relative] = temporary
            if validate:
                try:
                    entry["details"] = validate(files, entry["details"])
                except Exception:
                    entry["rejected"] = True
                    raise
                self.save()
            outputs = {}
            for relative, temporary in files.items():
                target = self.output_path(relative)
                expected = entry["prepared"][relative]["sha256"]
                if target.exists() or target.is_symlink():
                    if (previous_outputs.get(relative) != expected or not target.is_file()
                            or checksum(target) != expected):
                        raise ValueError("Untracked or modified output; refusing to overwrite: " + str(target))
                else:
                    entry["outputs"][relative] = expected
                    self.save()
                    atomic(target, temporary.read_bytes(), replace=False)
                outputs[relative] = expected
            entry.update(status="done", outputs=outputs, finished_at=now())
            entry.pop("prepared", None)
            entry.pop("staging", None)
            self.save()
            with contextlib.suppress(OSError):
                shutil.rmtree(stage)  # Only this receipt's successfully published staging directory.
        except BaseException as exc:
            entry.update(status="failed", error=str(exc), finished_at=now())
            self.save()
            raise
        self.save()
        return entry["details"]

    def write_readme(self, text):
        """Preserve human edits; adapters supply all descriptive content."""
        path = self.folder / "README.md"
        with contextlib.suppress(FileExistsError):
            atomic(path, text, replace=False)

    def verify_inputs_unchanged(self):
        for original, record in zip(self.source_paths(), self.state["sources"].values()):
            if checksum(original) != record["sha256"]:
                raise RuntimeError("Original changed during this job: " + str(original))

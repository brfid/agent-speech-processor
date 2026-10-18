"""Small local replay records; no downloads or provider-specific dependencies."""
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import zipfile

from .records import checksum
from .processes import run


# Whole modules that determine new recognition inputs, execution and acceptance.
# Playback, editorial rendering and experimental review stay in the full snapshot.
DRAFT_MODULES = ("audio.py", "drafts.py", "inputs.py", "job.py", "processes.py",
                 "provenance.py", "records.py", "whisper.py")


def library_identity(executable, dynamic=()):
    """Pin non-system Mach-O dependencies, including ggml/Metal, recursively.

    OS libraries are identified by the recorded OS release. On other platforms
    report the limit explicitly rather than claim an isolated runtime.
    """
    if sys.platform != "darwin":
        return {"libraries": {}, "unresolved": ["Dynamic-library inventory implemented for macOS only"]}
    dynamic = [Path(p).resolve() for p in dynamic]
    pending, seen = [Path(executable).resolve(), *dynamic], set()
    libraries, unresolved = {p.name: checksum(p) for p in dynamic}, set()
    while pending:
        binary = pending.pop()
        if binary in seen:
            continue
        seen.add(binary)
        result = run(["otool", "-L", binary], check=False)
        if result.returncode:
            unresolved.add(f"Cannot inspect {binary.name}")
            continue
        for line in result.stdout.decode().splitlines()[1:]:
            name = line.strip().split(" (", 1)[0]
            if name.startswith(("/usr/lib/", "/System/Library/")):
                continue
            if name.startswith("@loader_path/"):
                candidates = [binary.parent / name.removeprefix("@loader_path/")]
            elif name.startswith("@rpath/"):
                suffix = name.removeprefix("@rpath/")
                candidates = [binary.parent / suffix, binary.parent.parent / "lib" / suffix,
                              Path("/opt/homebrew/lib") / suffix, Path("/usr/local/lib") / suffix]
            elif name.startswith("@executable_path/"):
                candidates = [Path(executable).resolve().parent / name.removeprefix("@executable_path/")]
            else:
                candidates = [Path(name)]
            target = next((p.resolve() for p in candidates if p.is_file()), None)
            if target is None:
                unresolved.add(name)
                continue
            # Content identity is separate from installation locations.
            libraries[target.name] = checksum(target)
            pending.append(target)
    return {"libraries": dict(sorted(libraries.items())), "unresolved": sorted(unresolved)}


def tool_identity(name, executable, version_args):
    result = run([executable, *version_args], check=False)
    version = (result.stdout + result.stderr).decode(errors="replace").strip()
    dynamic = re.findall(r"^load_backend: loaded .+? backend from (.+)$", version, re.MULTILINE)
    if "--help" in version_args:
        position = version.lower().find("usage:")
        if position < 0:
            raise RuntimeError("Could not identify stable recognizer help; refusing an unstable replay recipe")
        # Startup contains GPU compile timings and transient memory information.
        # Pin actual loaded libraries separately, not those volatile messages.
        version = version[position:]
    version = version.replace(str(executable), name)
    return {"name": name, "sha256": checksum(executable), "version_or_help": version,
            **library_identity(executable, dynamic)}


def code_snapshot(folder=None):
    folder = Path(folder) if folder is not None else Path(__file__).parent
    files = {p.relative_to(folder).as_posix(): p.read_bytes() for p in sorted(folder.rglob("*.py"))}
    target = io.BytesIO()
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            info = zipfile.ZipInfo("agent_speech_processor/" + name, date_time=(1980, 1, 1, 0, 0, 0))
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o100644 << 16
            archive.writestr(info, content)
    return {name: hashlib.sha256(content).hexdigest() for name, content in files.items()}, target.getvalue()


def recipe(engine, model, model_receipt=None):
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if not ffmpeg or not ffprobe:
        raise FileNotFoundError("Install FFmpeg and FFprobe before drafting")
    model_info = {"name": model.name, "sha256": checksum(model), "bytes": model.stat().st_size}
    if model_receipt:
        receipt = json.loads(Path(model_receipt).read_text())
        if receipt.get("sha256") != model_info["sha256"]:
            raise ValueError("Model installation receipt does not match the selected weights")
        model_info.update({k: receipt[k] for k in ("repository_revision", "source_url") if k in receipt})
    hashes, snapshot = code_snapshot()
    environment = {"platform": platform.system(), "release": platform.release(),
                   "machine": platform.machine(), "python": platform.python_version(),
                   "macos": platform.mac_ver()[0],
                   "thread_environment": {k: os.environ[k] for k in
                       ("OMP_NUM_THREADS", "GGML_METAL_PATH_RESOURCES", "GGML_NO_METAL") if k in os.environ}}
    if sys.platform == "darwin":
        environment["hardware"] = {key: run(["sysctl", "-n", key]).stdout.decode().strip()
                                   for key in ("hw.model", "hw.memsize", "machdep.cpu.brand_string")}
    value = {"schema": 2, "model": model_info, "environment": environment,
             "code_sha256": hashes, "code_snapshot_sha256": hashlib.sha256(snapshot).hexdigest(),
             "draft_code_sha256": draft_code_identity(hashes),
             "tools": {"whisper-cli": tool_identity("whisper-cli", engine, ["--help"]),
                       "ffmpeg": tool_identity("ffmpeg", ffmpeg, ["-version"]),
                       "ffprobe": tool_identity("ffprobe", ffprobe, ["-version"])},
             "replay_limits": "Retained recognition JSON reconstructs text. Recreated PCM can be compared. "
                              "Fresh model and editorial runs need not be byte-identical."}
    return value, snapshot




def draft_code_identity(hashes):
    """Pin explicit recognition modules; fail closed on an incomplete snapshot."""
    missing = sorted(set(DRAFT_MODULES) - hashes.keys())
    if missing:
        raise ValueError("Draft source snapshot missing required modules: " + ", ".join(missing))
    return {name: hashes[name] for name in DRAFT_MODULES}

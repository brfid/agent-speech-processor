"""Exact-file source selection and optional iCloud materialization."""
import json
import os
from pathlib import Path
import shutil

from .processes import run

ICMAT = (os.environ.get("AGENT_SPEECH_PROCESSOR_ICMAT") or shutil.which("icmat")
         or str(Path.home() / "bin/icmat"))


def fetch(paths):
    result = run([ICMAT, "fetch", "--json", "--", *paths], timeout=1200, check=False)
    try:
        receipt = json.loads(result.stdout)
    except ValueError as exc:
        raise RuntimeError("icmat returned no JSON receipt: " + result.stderr.decode(errors="replace")[-1000:]) from exc
    counts = receipt.get("counts", {})
    ok = (result.returncode == 0 and receipt.get("success")
          and receipt.get("enumerationComplete") and not receipt.get("failures")
          and counts.get("filesVerified") == counts.get("regularFiles") == len(paths)
          and counts.get("datalessFiles") == 0)
    return receipt, bool(ok)


def prepare_sources(paths):
    paths = [Path(p).expanduser() for p in paths]
    if any(p.is_symlink() or not p.is_file() for p in paths):
        raise ValueError("Inputs must be existing regular files, not directories or symlinks")
    resolved = [p.resolve() for p in paths]
    if not resolved or len(resolved) != len(set(resolved)):
        raise ValueError("Supply a nonempty list of distinct source files in their intended order")
    return resolved


def materialize(paths, enabled):
    paths = list(dict.fromkeys(Path(p).resolve() for p in paths))
    if not paths:
        return None
    cloud = [p for p in paths if "com~apple~CloudDocs" in p.parts]
    if cloud and not enabled:
        raise ValueError("Use --fetch for exact iCloud inputs")
    if enabled:
        receipt, ok = fetch(paths)
        if not ok:
            raise RuntimeError("Exact-path icmat verification failed: " + json.dumps(receipt))
        return receipt
    return None

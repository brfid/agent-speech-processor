"""Bounded subprocess execution with process-group cancellation."""
import contextlib
import os
from pathlib import Path
import signal
import subprocess


def run(args, timeout=300, *, check=True, env=None):
    args = [str(a) for a in args]
    process = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               start_new_session=True, env=dict(os.environ, **(env or {})))
    try:
        stdout, stderr = process.communicate(timeout=timeout)
    except BaseException as exc:
        # Descendants may still hold the pipes after their parent exits.
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGTERM)
        try:
            stdout, stderr = process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(process.pid, signal.SIGKILL)
            stdout, stderr = process.communicate()
        # Allow a caller with durable staging to retain interrupted diagnostics.
        exc.stdout, exc.stderr, exc.returncode = stdout, stderr, process.returncode
        raise
    if check and process.returncode:
        raise RuntimeError(f"{Path(args[0]).name} ({process.returncode}): " + stderr.decode(errors="replace")[-2000:])
    return subprocess.CompletedProcess(args, process.returncode, stdout, stderr)

"""Small file-publication primitives shared by text workflows."""
from contextlib import contextmanager
import fcntl
import os

from .records import atomic


def regular(path):
    if path.is_symlink() or not path.is_file():
        raise ValueError(f"Expected a regular file, not a symlink: {path}")
    return path


@contextmanager
def lock(path):
    if path.exists() or path.is_symlink():
        regular(path)
    descriptor = os.open(path, os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
    with os.fdopen(descriptor, "a") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise RuntimeError(f"Another writer holds this folder: {path.parent}") from exc
        yield


def same_or_create(path, data):
    if path.exists() or path.is_symlink():
        if regular(path).read_bytes() != data:
            raise ValueError(f"Existing publication differs; evidence changed: {path}")
    else:
        atomic(path, data, replace=False)

"""Persistent POSIX flock ownership; file contents are diagnostics only."""
from __future__ import annotations

import fcntl
import json
import os
import stat
import threading
from dataclasses import dataclass
from pathlib import Path

from .paths import UnsafePathError, file_path


@dataclass
class _Owner:
    fd: int
    pid: int
    thread: int
    depth: int = 1


_owners: dict[Path, _Owner] = {}
_guard = threading.RLock()


def _after_fork() -> None:
    # Close inherited copies without LOCK_UN, which would unlock the parent's
    # shared open-file description. A child must acquire its own descriptor.
    global _guard
    for owner in _owners.values():
        os.close(owner.fd)
    _owners.clear()
    _guard = threading.RLock()


def _before_fork() -> None:
    _guard.acquire()


def _after_fork_parent() -> None:
    _guard.release()


os.register_at_fork(before=_before_fork, after_in_parent=_after_fork_parent, after_in_child=_after_fork)


def _check_lock_inode(path: Path, fd: int) -> None:
    metadata = os.fstat(fd)
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_nlink != 1:
        raise UnsafePathError(path, "workspace lock must be a regular file with exactly one link")


def acquire(path: Path, generated_at: str) -> None:
    path = file_path(path)
    with _guard:
        owner = _owners.get(path)
        if owner is not None:
            if (owner.pid, owner.thread) != (os.getpid(), threading.get_ident()):
                raise RuntimeError(f"busy: workspace lock held: {path}")
            owner.depth += 1
            return
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
        try:
            _check_lock_inode(path, fd)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError as exc:
                raise RuntimeError(f"busy: workspace lock held: {path}") from exc
            # Never replace or unlink this inode. Only its owner updates metadata.
            data = (json.dumps({"created_at": generated_at, "pid": os.getpid()}, sort_keys=True) + "\n").encode()
            os.ftruncate(fd, 0)
            with os.fdopen(os.dup(fd), "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
        except BaseException:
            os.close(fd)
            raise
        _owners[path] = _Owner(fd, os.getpid(), threading.get_ident())


def release(path: Path, expected_pid: int) -> None:
    # Ownership is held in memory, never inferred from mutable metadata.
    path = file_path(path)
    with _guard:
        owner = _owners.get(path)
        if owner is None or (owner.pid, owner.thread) != (os.getpid(), threading.get_ident()):
            return
        if expected_pid != owner.pid:
            return
        owner.depth -= 1
        if owner.depth:
            return
        del _owners[path]
        try:
            fcntl.flock(owner.fd, fcntl.LOCK_UN)
        finally:
            os.close(owner.fd)


def is_held(path: Path) -> bool:
    """Probe kernel ownership without creating a file or changing diagnostics."""
    path = file_path(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    except FileNotFoundError:
        return False
    try:
        _check_lock_inode(path, fd)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return True
        fcntl.flock(fd, fcntl.LOCK_UN)
        return False
    finally:
        os.close(fd)

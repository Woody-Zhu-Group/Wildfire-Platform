"""Append-only JSONL shadow log. Safe across threads and processes, rotated by size, never stores the API key.

Every write (rotation check plus append) runs under a lock on a sidecar file
(`<log>.lock`), so several uvicorn workers appending to the same log cannot
rotate at once or interleave partial lines. The lock uses fcntl.flock on POSIX
and msvcrt.locking on Windows. A rotated log file is only ever renamed, never
rewritten, so a reader that opened it before a rotation still sees whole lines.
"""

from __future__ import annotations

import json
import os
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import IO, Any, Iterator

try:  # POSIX
    import fcntl
except ImportError:  # Windows
    fcntl = None  # type: ignore[assignment]
    import msvcrt

from shared.db import REPO_ROOT


def resolve_log_path(path: str) -> Path:
    candidate = Path(path)
    if not candidate.is_absolute():
        candidate = REPO_ROOT / candidate
    return candidate


class ShadowLog:
    def __init__(self, path: str, max_bytes: int, backups: int = 5) -> None:
        self.path = resolve_log_path(path)
        self.max_bytes = max_bytes
        self.backups = backups
        self._lock = threading.Lock()
        self.lock_path = self.path.with_name(self.path.name + ".lock")
        self.path.parent.mkdir(parents=True, exist_ok=True)

    @contextmanager
    def _process_lock(self) -> Iterator[None]:
        """Exclusive lock shared by every process writing this log path."""
        with self.lock_path.open("a+b") as handle:
            _lock_file(handle)
            try:
                yield
            finally:
                _unlock_file(handle)

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str, ensure_ascii=False, sort_keys=True)
        for env in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY"):
            key = (os.environ.get(env) or "").strip()
            if key and key in line:
                line = line.replace(key, "[redacted]")
        payload = (line + "\n").encode("utf-8")
        with self._lock, self._process_lock():
            self._rotate_if_needed(len(payload))
            with self.path.open("ab") as handle:
                handle.write(payload)
                handle.flush()

    def read_records(self) -> list[dict[str, Any]]:
        records: list[dict[str, Any]] = []
        paths = [self.path]
        for index in range(1, self.backups + 1):
            rotated = self.path.with_name(self.path.name + f".{index}")
            paths.append(rotated)
        for path in paths:
            if not path.exists():
                continue
            for line in path.read_text(encoding="utf-8").splitlines():
                if not line.strip():
                    continue
                records.append(json.loads(line))
        return records

    def _rotate_if_needed(self, incoming: int) -> None:
        if not self.path.exists():
            return
        if self.path.stat().st_size + incoming <= self.max_bytes:
            return
        oldest = self.path.with_name(self.path.name + f".{self.backups}")
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backups - 1, 0, -1):
            source = self.path.with_name(self.path.name + f".{index}")
            target = self.path.with_name(self.path.name + f".{index + 1}")
            if source.exists():
                source.replace(target)
        self.path.replace(self.path.with_name(self.path.name + ".1"))


def _lock_file(handle: IO[bytes]) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        return
    # msvcrt locks a byte range at the current position. LK_LOCK blocks for up
    # to about ten seconds per attempt, so loop until the other worker is done.
    handle.seek(0)
    while True:
        try:
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
            return
        except OSError:
            continue


def _unlock_file(handle: IO[bytes]) -> None:
    if fcntl is not None:
        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        return
    handle.seek(0)
    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)

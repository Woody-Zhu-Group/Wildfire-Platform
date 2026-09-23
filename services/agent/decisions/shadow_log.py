"""Append-only JSONL shadow log. Thread-safe, rotated by size, never stores the API key."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path
from typing import Any

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
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, record: dict[str, Any]) -> None:
        line = json.dumps(record, default=str, ensure_ascii=False, sort_keys=True)
        key = os.environ.get("TYPESAFE_API_KEY") or ""
        if key and key in line:
            line = line.replace(key, "[redacted]")
        payload = (line + "\n").encode("utf-8")
        with self._lock:
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

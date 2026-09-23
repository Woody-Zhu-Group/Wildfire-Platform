"""Canonical JSON hashing so replay can prove two payloads are the same."""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_bytes(payload: Any) -> bytes:
    """Sorted keys, compact separators, UTF-8. Floats use json's default encoding."""
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    ).encode("utf-8")


def payload_hash(payload: Any) -> str:
    return hashlib.sha256(canonical_bytes(payload)).hexdigest()


def bytes_hash(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()

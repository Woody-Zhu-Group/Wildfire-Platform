"""Configurable filesystem roots for Wildfire Services.

The repo `.env` is loaded before any path is read, so a path set only in
`.env` takes effect (a process environment variable still wins). A relative
path resolves from the repo root, not the working directory, matching
`DATASET_DEMO_DATA_DIR` in `shared/db.py`.
"""

from __future__ import annotations

import os
from pathlib import Path

from shared.db import load_env

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SERVICE_ROOT = REPO_ROOT / "services" / "risk_forecasting"


def _env_path(name: str, default: Path) -> Path:
    load_env(override=False)
    raw = os.environ.get(name)
    if not raw:
        return default.resolve()
    path = Path(raw)
    if not path.is_absolute():
        path = REPO_ROOT / path
    return path.resolve()


def get_service_root() -> Path:
    return _env_path("RISK_FORECASTING_ROOT", DEFAULT_SERVICE_ROOT)


def get_data_dir() -> Path:
    return _env_path("RISK_FORECASTING_DATA_DIR", get_service_root() / "data")


def get_artifacts_dir() -> Path:
    return _env_path("RISK_FORECASTING_ARTIFACTS_DIR", get_service_root() / "artifacts")

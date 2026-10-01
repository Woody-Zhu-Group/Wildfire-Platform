"""Writable Agent state and evaluation traces live outside the checkout."""

import os
from pathlib import Path


def runtime_dir() -> Path:
    if os.name == "nt":
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData/Local"))
    else:
        base = Path(os.environ.get("XDG_STATE_HOME", Path.home() / ".local/state"))
    return base / "Wildfire-Platform"


def eval_runs_dir() -> Path:
    return Path(os.environ.get("WILDFIRE_EVAL_RUNS_DIR") or runtime_dir() / "eval/runs")

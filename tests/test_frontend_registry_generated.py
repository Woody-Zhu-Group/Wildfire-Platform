"""Generated frontend catalogs must match services.shared.dataset_registry."""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def _generator():
    path = ROOT / "scripts" / "generate_frontend_registry.py"
    spec = importlib.util.spec_from_file_location("generate_frontend_registry", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_generated_frontend_files_are_not_stale():
    gen = _generator()
    caveats = json.loads(gen.CAVEATS_PATH.read_text(encoding="utf-8"))
    datasets = json.loads(gen.DATASETS_PATH.read_text(encoding="utf-8"))
    naming = json.loads(gen.NAMING_PATH.read_text(encoding="utf-8"))
    assert caveats == gen.caveat_catalog()
    assert datasets == gen.dataset_catalog()
    assert naming == gen.naming_catalog()
    assert len(naming["california_counties"]) == 58
    assert naming["workspace_utilities"] == ["PG&E", "SCE", "SDG&E"]
    assert "epss_pge_only" in caveats
    assert "us_ignitions_sample" in caveats
    assert "calfire_missingness" not in caveats
    assert caveats["cpuc_utility_caused"].startswith("CPUC ignitions")
    assert any(item["key"] == "cpuc_ignitions" for item in datasets["datasets"])

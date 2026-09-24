"""Risk data paths read the repo .env before they are computed (issue #61). No DB."""

from __future__ import annotations

import importlib

import pytest

import shared.db as db
import shared.paths as paths

NAMES = ("RISK_FORECASTING_ROOT", "RISK_FORECASTING_DATA_DIR", "RISK_FORECASTING_ARTIFACTS_DIR")


@pytest.fixture
def fake_repo(tmp_path, monkeypatch):
    """A repo root whose .env is the only source of the risk paths."""
    for name in NAMES:
        # setenv then delenv, so teardown removes whatever load_dotenv sets.
        monkeypatch.setenv(name, "unset")
        monkeypatch.delenv(name)
    monkeypatch.setattr(db, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(db, "_ENV_LOADED", False)
    monkeypatch.setattr(paths, "REPO_ROOT", tmp_path)
    yield tmp_path
    # Put the real module constants back for later tests.
    monkeypatch.undo()
    db._ENV_LOADED = False
    import services.risk_forecasting.config as config

    importlib.reload(config)


def _reload_config():
    import services.risk_forecasting.config as config

    return importlib.reload(config)


def test_paths_set_only_in_dotenv_take_effect_and_resolve_from_the_repo_root(fake_repo):
    elsewhere = fake_repo / "elsewhere" / "artifacts"
    (fake_repo / ".env").write_text(
        "RISK_FORECASTING_DATA_DIR=custom/data\n"
        f"RISK_FORECASTING_ARTIFACTS_DIR={elsewhere.as_posix()}\n",
        encoding="utf-8",
    )
    config = _reload_config()
    assert config.DATA_DIR == (fake_repo / "custom" / "data").resolve()
    assert config.GRID_CSV == (fake_repo / "custom" / "data" / "grid_cells.csv").resolve()
    assert config.ARTIFACTS_DIR == elsewhere.resolve()
    assert config.PARAMS_PATH == (elsewhere / "cnhpp_params.npz").resolve()


def test_a_relative_service_root_from_dotenv_moves_both_defaults(fake_repo):
    (fake_repo / ".env").write_text("RISK_FORECASTING_ROOT=svc\n", encoding="utf-8")
    config = _reload_config()
    assert config.SERVICE_ROOT == (fake_repo / "svc").resolve()
    assert config.DATA_DIR == (fake_repo / "svc" / "data").resolve()
    assert config.ARTIFACTS_DIR == (fake_repo / "svc" / "artifacts").resolve()


def test_the_process_environment_still_wins_over_dotenv(fake_repo, monkeypatch):
    (fake_repo / ".env").write_text("RISK_FORECASTING_DATA_DIR=from_dotenv\n", encoding="utf-8")
    monkeypatch.setenv("RISK_FORECASTING_DATA_DIR", "from_process")
    config = _reload_config()
    assert config.DATA_DIR == (fake_repo / "from_process").resolve()


def test_without_any_setting_the_defaults_are_unchanged(fake_repo):
    config = _reload_config()
    assert config.SERVICE_ROOT == paths.DEFAULT_SERVICE_ROOT.resolve()
    assert config.DATA_DIR == (paths.DEFAULT_SERVICE_ROOT / "data").resolve()

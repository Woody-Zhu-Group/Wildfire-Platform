"""load_all keeps loading the other tables when the boundary load fails. No DB."""

from __future__ import annotations

from types import SimpleNamespace

import psycopg
import pytest

from db.loaders import load_all
from db.loaders.arcgis_polygons import GeometryGateError, SourceUnavailable

OTHER_LOADERS = [
    ("load_counties", "load", "counties"),
    ("load_circuits", "load", "circuits"),
    ("load_grid", "load", "grid_cells"),
    ("load_cpuc", "load_combined", "cpuc_ignitions"),
    ("load_cpuc", "load_with_time", "cpuc_ignitions_with_time"),
    ("load_calfire", "load", "calfire_incidents"),
    ("load_epss", "load", "epss_outages"),
    ("load_psps", "load_events", "psps_events"),
    ("load_psps", "load_event_circuits", "psps_event_circuits"),
    ("load_us_ignitions", "load", "us_ignitions"),
]


class FakeConn:
    def __init__(self, name):
        self.name = name
        self.broken = False
        self.closed = False

    def close(self):
        self.closed = True


@pytest.fixture
def harness(monkeypatch, tmp_path):
    calls: list[tuple[str, str]] = []
    conns: list[FakeConn] = []
    settings = SimpleNamespace(
        safe_target="test", user="test", dataset_demo_data_dir=tmp_path,
        risk_forecasting_data_dir=tmp_path, schema_sql=tmp_path / "schema.sql",
    )

    def fake_connect(_settings, autocommit=False):
        conn = FakeConn(f"conn{len(conns)}")
        conns.append(conn)
        return conn

    monkeypatch.setattr(load_all, "get_settings", lambda: settings)
    monkeypatch.setattr(load_all, "connect", fake_connect)
    monkeypatch.setattr(load_all, "apply_schema", lambda conn, path: None)
    monkeypatch.setattr(load_all, "run_validation", lambda conn: calls.append(("validate", conn.name)))
    monkeypatch.setattr(load_all.coverage, "write", lambda conn: calls.append(("coverage", conn.name)))
    for module, attr, table in OTHER_LOADERS:
        def stub(conn, _settings, _table=table):
            calls.append((_table, conn.name))
            return 1
        monkeypatch.setattr(getattr(load_all, module), attr, stub)
    return SimpleNamespace(calls=calls, conns=conns, monkeypatch=monkeypatch)


def _fail_boundaries(harness, exc, *, break_conn=False):
    def load(conn, _settings):
        if break_conn:
            conn.broken = True
        raise exc

    harness.monkeypatch.setattr(load_all.load_boundaries, "load", load)


@pytest.mark.parametrize(
    "exc",
    [
        psycopg.errors.InternalError_("GEOSUnion: TopologyException"),
        SourceUnavailable("no cache and no network"),
        GeometryGateError("hftd_tiers failed the geometry gate"),
    ],
    ids=["database-error", "no-source", "failed-gate"],
)
def test_a_boundary_failure_still_loads_every_other_table_and_exits_non_zero(harness, exc):
    _fail_boundaries(harness, exc)
    assert load_all.main() == 1
    loaded = [table for table, _ in harness.calls]
    assert loaded == [table for _, _, table in OTHER_LOADERS] + ["validate", "coverage"]
    assert len(harness.conns) == 1  # the connection was still usable


def test_a_broken_connection_is_reopened_for_the_other_tables(harness):
    _fail_boundaries(harness, psycopg.OperationalError("server closed the connection"), break_conn=True)
    assert load_all.main() == 1
    assert len(harness.conns) == 2
    assert {conn for _, conn in harness.calls} == {"conn1"}
    assert all(conn.closed for conn in harness.conns)


def test_other_errors_from_the_boundary_load_are_not_swallowed(harness):
    _fail_boundaries(harness, KeyError("bug"))
    with pytest.raises(KeyError):
        load_all.main()


def test_a_clean_boundary_load_exits_zero(harness):
    harness.monkeypatch.setattr(
        load_all.load_boundaries, "load",
        lambda conn, _settings: {"iou_territories": 6, "hftd_tiers": 2},
    )
    assert load_all.main() == 0
    assert [table for table, _ in harness.calls][-2:] == ["validate", "coverage"]

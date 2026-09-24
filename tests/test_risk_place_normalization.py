"""The risk service's /predict county and utility inputs use the shared normalizers.

No database: the connection is replaced with a recorder.
"""

from __future__ import annotations

from contextlib import contextmanager

import pytest

from services.risk_forecasting import place


class _Cursor:
    def __init__(self, log: list) -> None:
        self.log = log
        self._rows: list = []

    def execute(self, sql, params=None):
        self.log.append((" ".join(sql.split()), params))
        if "FROM wildfire.counties WHERE" in sql:
            self._rows = [(params[0],)]
        elif "FROM wildfire.iou_territories WHERE" in sql:
            self._rows = [(params[0],)]
        else:
            self._rows = [(1,), (2,), (3,)]

    def fetchone(self):
        return self._rows[0] if self._rows else None

    def fetchall(self):
        return list(self._rows)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Conn:
    def __init__(self, log: list) -> None:
        self.log = log

    def cursor(self):
        return _Cursor(self.log)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


@pytest.fixture
def db(monkeypatch):
    log: list = []

    @contextmanager
    def fake_connect(_settings=None):
        yield _Conn(log)

    monkeypatch.setattr(place, "connect", fake_connect)
    monkeypatch.setattr(place, "get_settings", lambda: None)
    return log


@pytest.mark.parametrize(
    "value,canonical",
    [("LA", "Los Angeles"), ("Butte Co.", "Butte"), ("butte county", "Butte"), ("Sacramento", "Sacramento")],
)
def test_predict_county_inputs_resolve_to_the_canonical_name(db, value, canonical):
    resolved = place.resolve_place(county=value)
    assert resolved.scope_type == "county"
    assert resolved.scope_name == f"{canonical} County"
    assert db[0][1] == (canonical,)


@pytest.mark.parametrize("value", ["Atlantis", "SB", "Buttes"])
def test_predict_rejects_an_unknown_county_before_touching_the_database(db, value):
    with pytest.raises(place.PlaceNotFound, match="(?i)unknown county") as info:
        place.resolve_place(county=value)
    assert db == []
    if value != "Atlantis":
        assert "Did you mean" in str(info.value)


@pytest.mark.parametrize(
    "value,code",
    [("PGE", "PGE"), ("pg&e", "PGE"), ("Pacific Gas and Electric", "PGE"), ("Southern California Edison", "SCE"), ("SDG&E", "SDGE")],
)
def test_predict_utility_inputs_resolve_to_the_code(db, value, code):
    resolved = place.resolve_place(utility=value)
    assert resolved.scope_type == "utility" and resolved.scope_name == code
    assert db[0][1] == (code,)


@pytest.mark.parametrize("value", ["NOT_AN_IOU", "Edison International", "Liberty", "BVES"])
def test_predict_rejects_utilities_the_fitted_model_does_not_cover(db, value):
    with pytest.raises(place.PlaceNotFound, match="(?i)unknown utility"):
        place.resolve_place(utility=value)
    assert db == []

"""db/loaders/validate.py counts CAL FIRE types against the registry default.

The default counts every incident except the registry's non-wildfire types
(``CALFIRE_NON_WILDFIRE_INCIDENT_TYPES``); untyped rows are counted. The
validation prints how many rows the default excludes, how many untyped rows
it counts, and any stored type in neither registry list. The first test
replaces the connection; the others read the warehouse.
"""

from __future__ import annotations

from db.loaders import validate
from services.shared.dataset_registry import (
    CALFIRE_NON_WILDFIRE_INCIDENT_TYPES,
    CALFIRE_REVIEWED_WILDFIRE_INCIDENT_TYPES,
    calfire_default_type_sql,
)

# Result width of each validation query, keyed by a word only that query has.
_WIDTHS = {
    "combined_n": 4,
    "null_utility": 4,
    "bad_circuits": 3,
    "leading_zero": 2,
    "tagged": 2,
}


class _Cursor:
    def __init__(self, executed: list[str]):
        self.executed = executed

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql, params=None):
        self.executed.append(sql)

    def fetchone(self):
        sql = self.executed[-1]
        width = next((n for word, n in _WIDTHS.items() if word in sql), 1)
        return (7,) * width

    def fetchall(self):
        if "DISTINCT incident_type" in self.executed[-1]:
            return [("Rescue",)]
        return []


class _Conn:
    def __init__(self):
        self.executed: list[str] = []

    def cursor(self):
        return _Cursor(self.executed)


def test_calfire_health_query_uses_the_registry_default(capsys):
    conn = _Conn()
    validate.run_validation(conn)
    calfire = [sql for sql in conn.executed if "null_utility" in sql]
    assert len(calfire) == 1
    assert calfire_default_type_sql("incident_type") in calfire[0]
    out = capsys.readouterr().out
    assert "incident_type excluded by the default (Earthquake, Flood, Hazmat): 7" in out
    assert "null incident_type (counted by the default): 7" in out
    assert "incident types not yet reviewed (counted by the default): Rescue" in out


def test_warehouse_default_excludes_exactly_the_non_wildfire_rows(db_conn):
    """Measured 2026-09-24: 2 Flood, 1 Earthquake, 1 Hazmat; untyped rows stay."""
    with db_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
              count(*) FILTER (WHERE NOT {calfire_default_type_sql("incident_type")}),
              count(*) FILTER (WHERE incident_type = ANY(%s)),
              count(*) FILTER (WHERE incident_type IS NULL
                                 AND {calfire_default_type_sql("incident_type")}),
              count(*) FILTER (WHERE incident_type IS NULL)
            FROM wildfire.calfire_incidents
            """,
            (list(CALFIRE_NON_WILDFIRE_INCIDENT_TYPES),),
        )
        excluded, non_wildfire, untyped_counted, untyped = cur.fetchone()
    assert excluded == non_wildfire == 4
    assert untyped_counted == untyped == 1234


def test_warehouse_has_no_unreviewed_incident_type(db_conn):
    """A new stored type needs a decision in services/shared/naming.py."""
    reviewed = list(CALFIRE_NON_WILDFIRE_INCIDENT_TYPES) + list(
        CALFIRE_REVIEWED_WILDFIRE_INCIDENT_TYPES
    )
    with db_conn.cursor() as cur:
        cur.execute(
            "SELECT DISTINCT incident_type FROM wildfire.calfire_incidents "
            "WHERE incident_type IS NOT NULL AND incident_type <> ALL(%s)",
            (reviewed,),
        )
        assert cur.fetchall() == []

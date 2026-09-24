"""db/loaders/validate.py counts CAL FIRE types against the registry default.

It used to count ``incident_type IS DISTINCT FROM 'Wildfire'``, so the 38
``Fire`` rows read as non-wildfire although every service counts them in the
default. The first test replaces the connection; the last reads the warehouse.
"""

from __future__ import annotations

from db.loaders import validate
from services.shared.dataset_registry import (
    CALFIRE_DEFAULT_INCIDENT_TYPES,
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
    assert "IS DISTINCT FROM 'Wildfire'" not in calfire[0]
    out = capsys.readouterr().out
    assert "incident_type outside the Wildfire,Fire default: 7" in out
    assert "non-wildfire incident_type" not in out


def test_warehouse_fire_rows_are_not_counted_outside_the_default(db_conn):
    with db_conn.cursor() as cur:
        cur.execute(
            f"""
            SELECT
              count(*) FILTER (WHERE incident_type = 'Fire'),
              count(*) FILTER (WHERE incident_type IS NOT NULL
                                   AND NOT {calfire_default_type_sql("incident_type")}),
              count(*) FILTER (WHERE incident_type IS NOT NULL
                                   AND incident_type <> ALL(%s))
            FROM wildfire.calfire_incidents
            """,
            (list(CALFIRE_DEFAULT_INCIDENT_TYPES),),
        )
        fire, outside, expected = cur.fetchone()
    assert fire > 0
    assert outside == expected

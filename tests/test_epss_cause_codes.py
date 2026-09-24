"""EPSS cause codes and word forms are one cause (written rule, 2026-09-24).

VEG and Vegetation, UNK and Unknown, 3RD and 3rd Party: filters match both
spellings, groupings merge them, and results show the word form. EF is
ambiguous between two equipment words and is left alone.
"""

from __future__ import annotations

from datetime import date

from services.data_query import queries as dq
from services.shared.dataset_registry import EPSS_CAUSE_CODE_WORDS, EPSS_CAUSE_CODES_LEFT_ALONE
from services.shared.epss_causes import cause_display_sql, cause_variants, cause_word
from services.visualization import queries as vq


def test_the_rule_lists_exactly_the_unambiguous_pairs():
    assert EPSS_CAUSE_CODE_WORDS == {"VEG": "Vegetation", "UNK": "Unknown", "3RD": "3rd Party"}
    assert set(EPSS_CAUSE_CODES_LEFT_ALONE) == {"EF"}
    assert not set(EPSS_CAUSE_CODE_WORDS) & set(EPSS_CAUSE_CODES_LEFT_ALONE)


def test_variants_and_word_forms():
    assert cause_variants("Vegetation") == ["Vegetation", "VEG"]
    assert cause_variants("VEG") == ["Vegetation", "VEG"]
    assert cause_variants("Animal") == ["Animal"]
    assert cause_variants("EF") == ["EF"]
    assert cause_word("UNK") == "Unknown"
    sql = cause_display_sql("e.cause")
    assert "WHEN 'VEG' THEN 'Vegetation'" in sql and "'EF'" not in sql


def _epss(conn, **kw):
    args = dict(circuit_id=None, utility=None, county=None, year=None, start_date=None, end_date=None,
                outage_type=None, cause=None, bbox=None, limit=1000, offset=0)
    args.update(kw)
    return dq.query_epss(conn, **args)


def _scalar(conn, sql):
    with conn.cursor() as cur:
        cur.execute(sql)
        return int(cur.fetchone()[0])


def test_filters_count_both_spellings_and_show_the_word(db_conn):
    for code, word in EPSS_CAUSE_CODE_WORDS.items():
        expected = _scalar(db_conn, f"SELECT COUNT(*) FROM wildfire.epss_outages WHERE cause IN ('{word}', '{code}')")
        _rows, total, _ = _epss(db_conn, cause=word, limit=1)
        assert total == expected > _scalar(db_conn, f"SELECT COUNT(*) FROM wildfire.epss_outages WHERE cause = '{word}'")
    rows, total, _ = _epss(db_conn, year=2021)
    assert total == len(rows) == 9
    assert {row["cause"] for row in rows} == {"Unknown", "Vegetation", "3rd Party", "EF"}


def test_grouped_counts_merge_codes_into_words(db_conn):
    grouped = dq.query_grouped_counts(
        db_conn, dataset="epss_outages", group_by="cause", utility=None, county=None,
        start_date=date(2021, 1, 1), end_date=date(2025, 12, 31),
    )
    keys = {row["key"]: row["value"] for row in grouped["rows"]}
    assert not {"VEG", "UNK", "3RD"} & set(keys)
    assert keys["Vegetation"] == 1043 and keys["Unknown"] == 3730 and keys["3rd Party"] == 915
    assert keys["EF"] == 1
    assert sum(keys.values()) == grouped["total"]


def test_map_outages_and_event_detail_show_the_word(db_conn):
    rows, _total, _ = vq.map_epss_circuits(
        db_conn, utility=None, year=2021, start_date=None, end_date=None, county=None,
        outage_type=None, cause="Unknown", bbox=None, limit=50, offset=0, include_outages=True,
    )
    outages = [o for row in rows for o in row["outages"]]
    assert len(outages) == 6 and {o["cause"] for o in outages} == {"Unknown"}
    record_id = _scalar(db_conn, "SELECT id FROM wildfire.epss_outages WHERE cause = 'VEG'")
    assert vq.event_detail(db_conn, "epss", str(record_id))["cause"] == "Vegetation"
    with db_conn.cursor() as cur:
        cur.execute("SELECT circuit_id FROM wildfire.epss_outages WHERE cause = 'UNK' LIMIT 1")
        circuit_id = cur.fetchone()[0]
    circuit = vq.event_detail(db_conn, "circuits", circuit_id, year=2021)
    assert "UNK" not in {o["cause"] for o in circuit["outages"]}
    assert "Unknown" in {o["cause"] for o in circuit["outages"]}

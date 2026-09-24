"""normalize_psps_utility raises on an unknown source spelling.

It used to pass an unknown spelling through, which would load a utility value
no filter matches. The last test checks the current source file has none.
"""

from __future__ import annotations

import json

import pytest

from db.loaders.util import UnknownPspsUtilityError, normalize_psps_utility
from services.shared.dataset_registry import UTILITY_CODES


@pytest.mark.parametrize("raw, code", [
    ("PGE", "PGE"),
    ("PG&E", "PGE"),
    (" pg & e ", "PGE"),
    ("SDG&E", "SDGE"),
    ("sce", "SCE"),
    ("Liberty", "Liberty"),
    ("LIBERTY", "Liberty"),
    ("PacifiCorp", "PACIFICORP"),
    ("BVES", "BVES"),
])
def test_known_spellings_map_to_codes(raw, code):
    assert normalize_psps_utility(raw) == code


@pytest.mark.parametrize("raw", ["Southern California Edison", "PG and E", "", None])
def test_unknown_spellings_raise(raw):
    with pytest.raises(UnknownPspsUtilityError, match="unknown PSPS IOU spelling"):
        normalize_psps_utility(raw)


def test_current_source_has_only_known_spellings(demo_data_dir):
    features = json.loads(
        (demo_data_dir / "psps_events.geojson").read_text(encoding="utf-8")
    )["features"]
    assert features
    codes = {normalize_psps_utility(f["properties"]["IOU"]) for f in features}
    assert codes <= set(UTILITY_CODES)

"""Resolve a place query to California grid cell IDs via PostGIS."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional, Sequence

from fastapi import HTTPException

from services.data_query.filters import parse_utility
from services.shared.counties import UnknownCountyError, normalize_county
from services.shared.dataset_registry import RISK_MODEL_UTILITIES
from shared.db import connect, get_settings

_RISK_UTILITIES = RISK_MODEL_UTILITIES
_CELL_ID_SAMPLE = 24


class PlaceNotFound(KeyError):
    """Unknown place or a point that does not fall in the risk grid."""


@dataclass(frozen=True)
class PlaceResolution:
    scope_type: str
    scope_name: str
    cell_ids: tuple[int, ...]

    @property
    def cell_count(self) -> int:
        return len(self.cell_ids)

    @property
    def includes_cell_461(self) -> bool:
        return 461 in self.cell_ids

    def cell_ids_for_response(self) -> Optional[list[int]]:
        if self.cell_count <= _CELL_ID_SAMPLE:
            return list(self.cell_ids)
        return None


def normalize_county_name(raw: str) -> str:
    """Canonical Census name for any spelling ("Butte Co.", "LA", "butte county").

    Uses the shared normalizer, so the risk service accepts exactly what the
    data query service accepts. An unknown value raises PlaceNotFound with
    the closest county names.
    """
    try:
        return normalize_county(raw)
    except UnknownCountyError as exc:
        raise PlaceNotFound(str(exc)) from exc


def normalize_utility_code(raw: str) -> str:
    """PGE, SCE, or SDGE from a code or a full name; anything else raises."""
    try:
        code = parse_utility(raw, allow_untagged=False)
    except HTTPException as exc:
        raise PlaceNotFound(str(exc.detail)) from exc
    if code not in _RISK_UTILITIES:
        raise PlaceNotFound(
            f"Unknown utility {raw!r} for fitted risk; it accepts PGE, SCE, or SDGE."
        )
    return str(code)


def resolve_place(
    *,
    cell_id: Optional[int] = None,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    county: Optional[str] = None,
    utility: Optional[str] = None,
    known_cell_ids: Optional[Sequence[int]] = None,
) -> PlaceResolution:
    """Accept exactly one of cell_id | lat+lon | county | utility."""
    groups: list[str] = []
    if cell_id is not None:
        groups.append("cell_id")
    if lat is not None or lon is not None:
        if lat is None or lon is None:
            raise ValueError("lat and lon must be provided together")
        groups.append("point")
    if county:
        groups.append("county")
    if utility:
        groups.append("utility")
    if len(groups) != 1:
        raise ValueError(
            "Provide exactly one of cell_id, lat+lon, county, or utility"
        )

    if cell_id is not None:
        cid = int(cell_id)
        if known_cell_ids is not None and cid not in set(int(x) for x in known_cell_ids):
            raise PlaceNotFound(f"Unknown cell_id={cid} (not in grid_cells.csv)")
        if cid < 0:
            raise PlaceNotFound(f"Unknown cell_id={cid} (not in grid_cells.csv)")
        return PlaceResolution("cell", f"cell {cid}", (cid,))

    if lat is not None and lon is not None:
        return _resolve_point(float(lat), float(lon))
    if county:
        return _resolve_county(str(county))
    return _resolve_utility(str(utility))


def _resolve_county(raw: str) -> PlaceResolution:
    name = normalize_county_name(raw)
    with connect(get_settings()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT name FROM wildfire.counties WHERE lower(name) = lower(%s)",
                (name,),
            )
            row = cur.fetchone()
            if row is None:
                raise PlaceNotFound(
                    f"Unknown county {raw!r}. Names match Census TIGER "
                    "(e.g. 'Sacramento' or 'Sacramento County')."
                )
            canonical = str(row[0])
            cur.execute(
                """
                SELECT g.cell_id FROM wildfire.grid_cells g
                JOIN wildfire.counties c ON ST_Intersects(g.geom, c.geom)
                WHERE lower(c.name) = lower(%s)
                ORDER BY g.cell_id
                """,
                (canonical,),
            )
            cell_ids = tuple(int(r[0]) for r in cur.fetchall())
    if not cell_ids:
        raise PlaceNotFound(f"No grid cells intersect {canonical} County")
    return PlaceResolution("county", f"{canonical} County", cell_ids)


def _resolve_utility(raw: str) -> PlaceResolution:
    code = normalize_utility_code(raw)
    with connect(get_settings()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT utility FROM wildfire.iou_territories WHERE utility = %s",
                (code,),
            )
            if cur.fetchone() is None:
                raise PlaceNotFound(
                    f"Unknown utility {raw!r}. Fitted risk accepts PGE, SCE, or SDGE."
                )
            cur.execute(
                """
                SELECT g.cell_id FROM wildfire.grid_cells g
                JOIN wildfire.iou_territories i ON ST_Intersects(g.geom, i.geom)
                WHERE i.utility = %s
                ORDER BY g.cell_id
                """,
                (code,),
            )
            cell_ids = tuple(int(r[0]) for r in cur.fetchall())
    if not cell_ids:
        raise PlaceNotFound(f"No grid cells intersect {code} territory")
    return PlaceResolution("utility", code, cell_ids)


def _resolve_point(lat: float, lon: float) -> PlaceResolution:
    with connect(get_settings()) as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT g.cell_id FROM wildfire.grid_cells g
                WHERE ST_Contains(g.geom, ST_SetSRID(ST_MakePoint(%s, %s), 4326))
                LIMIT 1
                """,
                (lon, lat),
            )
            row = cur.fetchone()
    if row is None:
        raise PlaceNotFound(
            f"Point ({lat}, {lon}) is outside the California risk grid"
        )
    cid = int(row[0])
    return PlaceResolution("point", f"{lat}, {lon}", (cid,))

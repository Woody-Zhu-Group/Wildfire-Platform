"""Harness-computed arithmetic over cited counts.

Grounding only lets synthesis state numbers that appear in evidence, so a
question that asks how much a count changed could never get its answer: the
difference is not in any tool result, and the model must not compute it. When
the question asks for a change, difference, percent change, or ratio, this
module computes those values from the successful primary count results and
returns them as one evidence item with its own id and the ids it came from.
Synthesis cites that id like any other evidence.

Pairs are formed only between counts of the same measure:
- the same entity (same dataset, utility, county, tier, filters) across two
  periods, earliest period first;
- two entities in the same period, when the period has exactly two.
Nothing is derived from qualification companions.
"""

from __future__ import annotations

import re
import uuid
from dataclasses import dataclass
from datetime import date
from typing import Any

from services.agent.tools import ToolExecution

DERIVED_TOOL = "harness_arithmetic"

_CHANGE_RE = re.compile(
    r"\b(?:chang(?:e|ed|es|ing)|differen(?:ce|ces|t)|differ(?:ed|s)?|"
    r"increas(?:e|ed|es|ing)|decreas(?:e|ed|es|ing)|ris(?:e|en|ing)|rose|"
    r"f[ae]ll|falling|drop(?:ped|s)?|gr[eo]w(?:n|th)?|declin(?:e|ed|es|ing)|"
    r"up or down|how much (?:more|less|fewer|higher|lower)|"
    r"how many (?:more|fewer)|by how much|gap)\b",
    re.IGNORECASE,
)
_PERCENT_RE = re.compile(r"\bpercent(?:age)?\b|%", re.IGNORECASE)
_RATIO_RE = re.compile(
    r"\bratio\b|\btimes (?:as (?:many|much|high)|more|higher)\b|\b(?:double|triple)d?\b|\bfold\b",
    re.IGNORECASE,
)


def requested_operations(question: str) -> set[str]:
    """Arithmetic the question asks for: difference, percent_change, ratio."""
    text = question or ""
    ops: set[str] = set()
    if _PERCENT_RE.search(text):
        ops.update({"difference", "percent_change"})
    if _RATIO_RE.search(text):
        ops.add("ratio")
    if _CHANGE_RE.search(text):
        ops.add("difference")
    return ops


@dataclass(frozen=True)
class _Cell:
    measure: str
    entity: str
    start: str
    end: str
    value: float
    evidence_id: str

    @property
    def period(self) -> str:
        return period_label(self.start, self.end)


def period_label(start: str | None, end: str | None) -> str:
    """"2020" for a full calendar year, otherwise "start to end"."""
    if not start or not end:
        return "all dates"
    if start[:4] == end[:4] and start[5:] == "01-01" and end[5:] == "12-31":
        return start[:4]
    return f"{start} to {end}"


def _date_text(value: Any) -> str | None:
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
        return value
    return None


def _window(args: dict[str, Any]) -> tuple[str, str] | None:
    year = args.get("year")
    if isinstance(year, int):
        return f"{year}-01-01", f"{year}-12-31"
    start, end = _date_text(args.get("start_date")), _date_text(args.get("end_date"))
    if start and end:
        return start, end
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _cells(execution: ToolExecution) -> list[_Cell]:
    args = execution.arguments or {}
    summary = execution.summary or {}
    eid = execution.evidence_id
    if execution.tool == "data_query_records" and summary.get("result_mode") == "count":
        window = _window(args)
        value = _number(summary.get("total"))
        if window is None or value is None:
            return []
        dataset = str(summary.get("dataset") or args.get("dataset"))
        filters = [
            f"{key}={args[key]}"
            for key in ("utility", "county", "tier", "circuit_id", "min_acres", "incident_type_mode", "bbox")
            if args.get(key) not in (None, "")
        ]
        entity = ", ".join(filters) or "all"
        return [_Cell(f"{dataset} count", entity, *window, value, eid)]
    if execution.tool != "comparison_run":
        return []
    metric = str(summary.get("metric") or args.get("metric"))
    measure = "|".join(
        [
            metric,
            str(args.get("normalize") or "none"),
            str(args.get("ignition_definition") or "attribute"),
        ]
    )
    kind = args.get("kind") or summary.get("kind")
    if kind in {"utilities", "regions"}:
        window = _window(args)
        if window is None:
            return []
        prefix = "utility" if kind == "utilities" else str(args.get("region_type") or "region")
        cells = []
        for row in summary.get("results") or []:
            value = _number(row.get("value"))
            if value is None or row.get("key") in (None, ""):
                continue
            cells.append(_Cell(measure, f"{prefix}={row['key']}", *window, value, eid))
        return cells
    if kind == "periods":
        entity = f"{args.get('scope_type')}={args.get('scope')}"
        cells = []
        for key in ("a", "b"):
            start = _date_text(args.get(f"period_{key}_start"))
            end = _date_text(args.get(f"period_{key}_end"))
            value = _number((summary.get(f"period_{key}") or {}).get("value"))
            if start and end and value is not None:
                cells.append(_Cell(measure, entity, start, end, value, eid))
        return cells
    return []


def _round(value: float, places: int) -> int | float:
    rounded = round(value, places)
    return int(rounded) if float(rounded).is_integer() else rounded


def _derive(base: _Cell, other: _Cell, ops: set[str], *, basis: str) -> dict[str, Any]:
    """Arithmetic from base to other. For a change, base is the earlier period."""
    difference = other.value - base.value
    row: dict[str, Any] = {
        "basis": basis,
        "measure": base.measure,
        "from": {"entity": base.entity, "period": base.period, "value": _round(base.value, 4)},
        "to": {"entity": other.entity, "period": other.period, "value": _round(other.value, 4)},
        "source_evidence_ids": sorted({base.evidence_id, other.evidence_id}),
    }
    if "difference" in ops or "percent_change" in ops:
        row["difference"] = _round(difference, 4)
        row["absolute_difference"] = _round(abs(difference), 4)
        if basis == "change_over_time":
            row["direction"] = (
                "increase" if difference > 0 else "decrease" if difference < 0 else "no change"
            )
        else:
            # Two entities in one period: neither rose nor fell, one is larger.
            row["larger"] = (
                other.entity if difference > 0 else base.entity if difference < 0 else "equal"
            )
    if "percent_change" in ops:
        if base.value == 0:
            row["percent_change"] = None
            row["percent_change_reason"] = "undefined because the starting value is 0"
        else:
            pct = difference / base.value * 100
            row["percent_change"] = _round(pct, 1)
            row["absolute_percent_change"] = _round(abs(pct), 1)
    if "ratio" in ops:
        if base.value == 0:
            row["ratio"] = None
            row["ratio_reason"] = "undefined because the starting value is 0"
        else:
            row["ratio"] = _round(other.value / base.value, 2)
    return row


def derive_arithmetic(question: str, executions: list[ToolExecution]) -> ToolExecution | None:
    """One evidence item with every requested value, or None when nothing pairs."""
    ops = requested_operations(question)
    if not ops:
        return None
    cells: dict[tuple[str, str, str, str], _Cell] = {}
    for execution in executions:
        if not execution.ok or execution.qualification_call:
            continue
        for cell in _cells(execution):
            cells.setdefault((cell.measure, cell.entity, cell.start, cell.end), cell)

    rows: list[dict[str, Any]] = []
    by_entity: dict[tuple[str, str], list[_Cell]] = {}
    by_period: dict[tuple[str, str, str], list[_Cell]] = {}
    for cell in cells.values():
        by_entity.setdefault((cell.measure, cell.entity), []).append(cell)
        by_period.setdefault((cell.measure, cell.start, cell.end), []).append(cell)

    for series in by_entity.values():
        ordered = sorted(series, key=lambda item: (item.start, item.end))
        for earlier, later in zip(ordered, ordered[1:]):
            rows.append(_derive(earlier, later, ops, basis="change_over_time"))
    for group in by_period.values():
        if len(group) != 2:
            continue
        first, second = sorted(group, key=lambda item: item.entity)
        rows.append(_derive(first, second, ops, basis="difference_between_entities"))
    if not rows:
        return None
    sources = sorted({eid for row in rows for eid in row["source_evidence_ids"]})
    return ToolExecution(
        tool=DERIVED_TOOL,
        arguments={"operations": sorted(ops), "source_evidence_ids": sources},
        ok=True,
        summary={
            "kind": "derived_arithmetic",
            "note": (
                "Computed by the harness from the cited counts, not by the model. "
                "difference is to minus from; percent_change is relative to from. "
                "direction applies to one entity over time; larger names the bigger "
                "of two entities in the same period."
            ),
            "derivations": rows,
        },
        raw=None,
        error=None,
        artifact=None,
        latency_ms=0.0,
        evidence_id=f"evidence_derived_{uuid.uuid4().hex[:12]}",
    )


def derived_quantity_values(executions: list[ToolExecution]) -> set[int]:
    """Whole-number derived values a brief may state as a quantity ("90 more ignitions")."""
    values: set[int] = set()
    for execution in executions:
        if not execution.ok or execution.tool != DERIVED_TOOL:
            continue
        for row in execution.summary.get("derivations") or []:
            for key in ("difference", "absolute_difference"):
                value = row.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    values.add(value)
    return values


def render_derived(summary: dict[str, Any]) -> str:
    """Plain sentences for the deterministic fallback answer."""
    parts: list[str] = []
    for row in summary.get("derivations") or []:
        start, end = row["from"], row["to"]
        if row["basis"] == "change_over_time":
            head = f"{end['entity']} from {start['period']} to {end['period']}"
        else:
            head = f"{end['entity']} minus {start['entity']} in {start['period']}"
        bits = []
        if "difference" in row:
            bits.append(f"difference {row['difference']:+,}")
        if row.get("percent_change") is not None:
            bits.append(f"percent change {row['percent_change']:+}%")
        if row.get("ratio") is not None:
            bits.append(f"ratio {row['ratio']}")
        if bits:
            parts.append(f"{head}: {', '.join(bits)}.")
    return " ".join(parts)

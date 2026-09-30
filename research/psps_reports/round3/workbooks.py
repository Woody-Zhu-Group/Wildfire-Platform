"""Parse circuit-level de-energization and restoration times from event workbooks.

SDG&E workbooks: sheet "Table 3" (Circuits De-energized) with combined
date/time columns. SCE workbooks: sheet "T05" (Circuits De-Energized) with
separate date and time columns. The parser finds, in any sheet, a header row
with a de-energization column and a restoration column (never an "All Clear"
column), reads every data row below it, and returns the earliest
de-energization and latest restoration with the sheet and row they came from.
"""

from __future__ import annotations

import re
from datetime import date, datetime, time
from pathlib import Path


def _as_datetime(date_value, time_value=None):
    if isinstance(date_value, datetime) and time_value is None:
        return date_value
    day = None
    if isinstance(date_value, datetime):
        day = date_value.date()
    elif isinstance(date_value, date):
        day = date_value
    elif isinstance(date_value, str):
        text = date_value.strip()
        for fmt in ("%m/%d/%Y %H:%M", "%m/%d/%Y %H:%M:%S", "%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%m/%d/%Y", "%m/%d/%y"):
            try:
                parsed = datetime.strptime(text, fmt)
            except ValueError:
                continue
            if time_value is None:
                return parsed
            day = parsed.date()
            break
    if day is None:
        return None
    if time_value is None:
        return None
    if isinstance(time_value, datetime):
        clock = time_value.time()
    elif isinstance(time_value, time):
        clock = time_value
    elif isinstance(time_value, str):
        match = re.search(r"(\d{1,2}):?(\d{2})", time_value)
        if not match:
            return None
        clock = time(int(match.group(1)) % 24, int(match.group(2)))
    elif isinstance(time_value, (int, float)):
        value = int(time_value)
        clock = time((value // 100) % 24, value % 100) if value > 24 else time(value % 24, 0)
    else:
        return None
    return datetime.combine(day, clock)


def _columns(header: list) -> dict | None:
    names = [str(h or "").lower().replace("\n", " ") for h in header]
    def find(*needles, exclude=("all clear", "all-clear")):
        return [i for i, n in enumerate(names) if all(k in n for k in needles) and not any(x in n for x in exclude)]
    de = find("de-energization") or find("de-energized") or find("de- energization")
    rs = find("restoration")
    if not de or not rs:
        return None
    cols = {}
    de_date = [i for i in de if "date" in names[i]]
    de_time = [i for i in de if "time" in names[i] and "date" not in names[i]]
    rs_date = [i for i in rs if "date" in names[i]]
    rs_time = [i for i in rs if "time" in names[i] and "date" not in names[i]]
    cols["de"] = (de_date[0] if de_date else de[0], de_time[0] if de_time else None)
    cols["rs"] = (rs_date[0] if rs_date else rs[0], rs_time[0] if rs_time else None)
    return cols


def circuit_times(path: Path) -> dict:
    """Earliest de-energization and latest restoration across all circuit rows."""
    import openpyxl

    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    best = None
    for sheet in workbook.worksheets:
        # Header rows sit near the top; stop reading a sheet after 25 empty rows,
        # because some workbooks format about a million empty rows.
        cols = None
        starts, ends = [], []
        empty = 0
        for number, row in enumerate(sheet.iter_rows(max_row=20000, values_only=True), start=1):
            if cols is None:
                if number > 30:
                    break
                cols = _columns(list(row))
                header_row = number
                continue
            if not any(v not in (None, "") for v in row):
                empty += 1
                if empty >= 25:
                    break
                continue
            empty = 0
            (dd, dt), (rd, rt) = cols["de"], cols["rs"]
            if dd >= len(row):
                continue
            start = _as_datetime(row[dd], row[dt] if dt is not None else None)
            end = _as_datetime(row[rd], row[rt] if rt is not None else None) if rd < len(row) else None
            if start:
                starts.append((start, number))
            if end:
                ends.append((end, number))
        if starts and (best is None or len(starts) > best["circuit_rows"]):
            first = min(starts)
            last = max(ends) if ends else (None, None)
            best = {
                "sheet": sheet.title,
                "header_row": header_row,
                "circuit_rows": len(starts),
                "first_deenergization": first[0].strftime("%Y-%m-%d %H:%M"),
                "first_row": first[1],
                "last_restoration": last[0].strftime("%Y-%m-%d %H:%M") if last[0] else None,
                "last_row": last[1],
            }
    return best or {}


if __name__ == "__main__":
    import json
    import sys

    for p in sys.argv[1:]:
        print(Path(p).name, json.dumps(circuit_times(Path(p))))

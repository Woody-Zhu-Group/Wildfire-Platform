"""Build ca_places_gazetteer_2025.csv from the Census Gazetteer places file.

Usage (repo root):
    python data/places/build_ca_places.py path/to/2025_gaz_place_06.txt

Source: https://www2.census.gov/geo/docs/maps-data/data/gazetteer/2025_Gazetteer/2025_gaz_place_06.txt
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

# Census LSAD codes for California places.
PLACE_TYPES = {"25": "city", "43": "town", "57": "CDP"}
SUFFIXES = {"city": " city", "town": " town", "CDP": " CDP"}

OUT = Path(__file__).with_name("ca_places_gazetteer_2025.csv")


def main(source: str) -> None:
    with open(source, encoding="utf-8", newline="") as handle:
        reader = csv.DictReader(handle, delimiter="|")
        rows = []
        for row in reader:
            if row["USPS"].strip() != "CA":
                continue
            place_type = PLACE_TYPES[row["LSAD"].strip()]
            name = row["NAME"].strip()
            suffix = SUFFIXES[place_type]
            if not name.endswith(suffix):
                raise SystemExit(f"Unexpected place name suffix: {name!r}")
            rows.append(
                {
                    "geoid": row["GEOID"].strip(),
                    "name": name[: -len(suffix)],
                    "place_type": place_type,
                    "lat": f"{float(row['INTPTLAT']):.6f}",
                    "lon": f"{float(row['INTPTLONG'].strip()):.6f}",
                }
            )
    with OUT.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(
            handle, fieldnames=["geoid", "name", "place_type", "lat", "lon"]
        )
        writer.writeheader()
        writer.writerows(rows)
    print(f"wrote {len(rows)} rows to {OUT}")


if __name__ == "__main__":
    main(sys.argv[1])

"""Record and check the gitignored round 3 inputs that round 4 reads (added after the PR #29 review, 2026-09-28).

round3/pages/, round3/raw/, and round3/raw_amend/ are gitignored (README.md, "Gitignored inputs").
Round 4 reads, for each of the 155 events:
- round3/pages/<report_id>.jsonl: page text, for the quote checks, apply.py, and the packets;
- the extracted document (round3/raw/ or round3/raw_amend/, per versions.csv): page images in the packets;
- every partial correction letter (round3/raw_amend/): correction text for the checks and the packets.

`write` records a SHA-256 of each file in inputs_sha256.json. `check` compares regenerated files with it.
Hashes ignore CRLF versus LF, since the page files were written on Windows.

    python research/psps_reports/round4/inputs.py check
"""

from __future__ import annotations

import argparse
import json

import packets as pk

HERE = pk.HERE
RECORD = HERE / "inputs_sha256.json"


def input_files() -> list[str]:
    """Paths relative to round3/, sorted."""
    files = set()
    for rid, v in pk.versions().items():
        files.add(f"pages/{rid}.jsonl")
        files.add(v["document"])
        for url in v["partial_corrections"].split(";"):
            if url.strip():
                files.add("raw_amend/" + url.strip().rsplit("/", 1)[-1])
    return sorted(files)


def main() -> None:
    import fitz

    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["write", "check"])
    args = ap.parse_args()
    if args.command == "write":
        record = {"pymupdf": fitz.VersionBind,
                  "files": {f: pk.file_sha256(pk.R3 / f) for f in input_files()}}
        RECORD.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
        print(f"recorded {len(record['files'])} files")
        return
    record = json.loads(RECORD.read_text(encoding="utf-8"))
    missing = [f for f in record["files"] if not (pk.R3 / f).exists()]
    differ = [f for f, h in record["files"].items() if f not in missing and pk.file_sha256(pk.R3 / f) != h]
    if fitz.VersionBind != record["pymupdf"]:
        print(f"note: PyMuPDF {fitz.VersionBind}; the hashes were recorded with {record['pymupdf']} installed")
    for f in missing:
        print("MISSING", f)
    for f in differ:
        print("DIFFERS", f)
    print(f"{len(record['files'])} files: {len(record['files']) - len(missing) - len(differ)} match, "
          f"{len(missing)} missing, {len(differ)} differ")
    raise SystemExit(1 if missing or differ else 0)


if __name__ == "__main__":
    main()

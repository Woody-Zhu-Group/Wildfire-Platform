"""Download the PSPS post-event reports in sources.csv and extract text by page.

PDFs go to raw/ (gitignored, about 80 MB). Writes pages/<report_id>.jsonl with
one {"page": n, "text": ...} object per PDF page (1-indexed, matching the PDF
viewer page number).

    python research/psps_reports/extract_pages.py
"""

from __future__ import annotations

import csv
import json
import urllib.request
from pathlib import Path

import fitz  # PyMuPDF

HERE = Path(__file__).resolve().parent


def main() -> None:
    with open(HERE / "sources.csv", newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    (HERE / "pages").mkdir(exist_ok=True)
    (HERE / "raw").mkdir(exist_ok=True)
    for row in rows:
        pdf_path = HERE / "raw" / f"{row['report_id']}.pdf"
        if not pdf_path.exists():
            request = urllib.request.Request(row["source_url"], headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(request, timeout=120) as response:
                pdf_path.write_bytes(response.read())
        doc = fitz.open(pdf_path)
        out = HERE / "pages" / f"{row['report_id']}.jsonl"
        with open(out, "w", encoding="utf-8") as fh:
            for index, page in enumerate(doc, start=1):
                fh.write(json.dumps({"page": index, "text": page.get_text()}) + "\n")
        print(row["report_id"], doc.page_count)


if __name__ == "__main__":
    main()

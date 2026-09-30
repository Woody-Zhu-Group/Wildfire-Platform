"""Find table pages whose table is an image, render them, and transcribe them.

Detection (designed on the 25 reports read in rounds 1 and 2):
- a page with a "Table N" caption, an embedded image covering at least 4
  percent of the page or at least 20 vector drawings (tables drawn as
  outlines have no text layer), and fewer than 12 numbers in the text between the caption and
  the next numbered item (so the table's numbers are not in the text layer); or
- a near-empty page (under 60 characters of text) with an image covering at
  least 12 percent of the page, in the first 60 percent of the report (a
  scanned page; later pages are mostly appendices).
At most MAX_PER_REPORT pages per report are transcribed; the rest are listed
as unread and flagged.

Transcription uses GPT-6 Luna (image input). It must return the table text or
exactly UNREADABLE. Unreadable pages go to the review queue.
"""

from __future__ import annotations

import base64
import json
import re
from pathlib import Path

MAX_PER_REPORT = 8
CAPTION = re.compile(r"Table\s*\d+[A-Z]?\s*[:.\-‐–]")


def detect(pdf_path: Path) -> list[dict]:
    import fitz

    doc = fitz.open(pdf_path)
    found = []
    for index, page in enumerate(doc):
        area = page.rect.width * page.rect.height
        images = [b for b in page.get_image_info() if b.get("bbox")]
        biggest = max(((b["bbox"][2] - b["bbox"][0]) * (b["bbox"][3] - b["bbox"][1]) / area for b in images), default=0.0)
        text = page.get_text()
        captions = list(CAPTION.finditer(text))
        drawn = len(page.get_drawings()) if captions else 0
        if captions and (biggest >= 0.04 or drawn >= 20):
            for cap in captions:
                line_end = text.find("\n", cap.end())
                caption_line = text[cap.start(): line_end if line_end > 0 else cap.end() + 80]
                if re.search(r"\bmap\b|figure", caption_line, re.I):
                    continue
                following = text[cap.end(): cap.end() + 1500]
                # Stop at the next numbered template item or section heading.
                stop = re.search(r"\n\s*(\d{1,2}\.\s+[A-Z]|Section\s+\d)", following)
                if stop:
                    following = following[: stop.start()]
                if len(re.findall(r"\b\d[\d,]*\b", following)) < 12:
                    found.append({"page": index + 1, "kind": "image_table", "image_share": round(biggest, 2), "caption": " ".join(caption_line.split())[:80]})
                    break
        elif len(text.strip()) < 60 and biggest >= 0.12 and index < doc.page_count * 0.6:
            found.append({"page": index + 1, "kind": "scanned_page", "image_share": round(biggest, 2), "caption": ""})
    return found


PROMPT = (
    "This is one page of a California utility PSPS post-event report. Transcribe every table "
    "on the page as plain text, one row per line, cells separated by ' | ', header row first. "
    "Copy every number exactly as printed. Also transcribe any other readable text on the page "
    "below the tables. If the page image is not legible enough to copy the numbers with "
    "confidence, reply with exactly UNREADABLE and nothing else."
)


def transcribe(pdf_path: Path, page_number: int, png_dir: Path, call) -> tuple[str, dict]:
    """Render one page and ask Luna to transcribe it. `call` posts a chat body and returns (text, usage)."""
    import fitz

    png_dir.mkdir(parents=True, exist_ok=True)
    png = png_dir / f"{pdf_path.stem}_p{page_number}.png"
    if not png.exists():
        fitz.open(pdf_path)[page_number - 1].get_pixmap(dpi=150).save(png)
    data = base64.b64encode(png.read_bytes()).decode()
    body = {
        "model": "openai/gpt-6-luna",
        "temperature": 0,
        "messages": [{"role": "user", "content": [
            {"type": "text", "text": PROMPT},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{data}"}},
        ]}],
        "usage": {"include": True},
    }
    return call(body)


if __name__ == "__main__":
    import sys

    for path in sys.argv[1:]:
        print(Path(path).name, json.dumps(detect(Path(path))))

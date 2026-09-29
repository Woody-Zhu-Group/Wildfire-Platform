"""Build review packets for round 4 (model-assisted review of the round 3 queue).

A packet is a folder outside the repository holding only what one reviewer
session may see for one report:

    items.json                 the session's items, without Jev's answer or confidence
    instructions.md            the review guide sections plus the answer format
    pages/NNN.txt              the text layer of every page of the report
    pages/NNN_transcription.txt  the stored transcription (round3/runs/images.jsonl)
    pages/NNN.png              image-only, low-text, or scrambled pages, and every page_ref
    correction/NNN.txt ...     correction letter pages (partial-correction items only)
    current_values.json        the event's current values (special-item packets only)

Reviewer 1 (Claude Code) reads the folder directly. Reviewer 2 (Sol) reads the
same folder through tools in run_sol.py, so both see identical material.

Item sets:
    dev    the 10 round 1 pilot reports: all nine fields, plus special items 9, 21, 25
    test   the 24 clean test reports (round 2 minus r2_pge_2017_2019, and round 3): nine fields
    queue  every queue item not settled by a test answer and not a redline

    python research/psps_reports/round4/packets.py build --set dev
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
R3 = ROOT / "round3"
sys.path.insert(0, str(ROOT))

from common import is_garbled  # noqa: E402

PACKET_ROOT = Path(os.environ.get("PSPS_R4_PACKETS", Path(tempfile.gettempdir()) / "psps_r4"))
CAT = ["mbl_advance_notice", "wind_threshold_cited", "complaints_reported", "claims_reported", "canceled_after_notice"]
NUM = ["customers_deenergized", "first_deenergization", "last_restoration", "counties_deenergized"]
FIELDS = CAT + NUM
SPECIAL_REASONS = ("conflicting_sources", "unreadable_page")
DEV_SPECIAL_ITEMS = ["9", "21", "25"]
EXCLUDED_TEST_REPORTS = {"r2_pge_2017_2019"}  # round 3 used the amended PDF (DESIGN.md, changes before the freeze)
LOW_TEXT_CHARS = 200
DPI = 150
# The only keys a reviewer ever sees for an item. Jev's answer and confidence never pass.
ITEM_KEYS = ["item", "reason", "field", "question", "options", "page_refs", "detail"]
FORBIDDEN = {"answer", "confidence", "jev_answer", "jev_confidence", "gold"}

NUMBER_QUESTIONS = {
    "customers_deenergized": (
        "How many customers were de-energized in this event? Give the event total as the report states it. "
        "Use 0 if nobody was shut off.", "whole number, or null"),
    "first_deenergization": (
        "When did the first customers lose power in this event?", "YYYY-MM-DD HH:MM (24-hour local time), or null"),
    "last_restoration": (
        "When did the last de-energized customers get power back in this event?", "YYYY-MM-DD HH:MM (24-hour local time), or null"),
    "counties_deenergized": (
        "In how many counties did customers actually lose power in this event?", "whole number, or null"),
}


# ------------------------------------------------------------------ inputs

def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def versions() -> dict[str, dict]:
    return {r["report_id"]: r for r in read_csv(R3 / "versions.csv")}


def queue() -> list[dict]:
    return read_csv(R3 / "review_queue.csv")


def report_pages(report_id: str) -> list[str]:
    with open(R3 / "pages" / f"{report_id}.jsonl", encoding="utf-8") as fh:
        rows = [json.loads(line) for line in fh]
    return [r["text"] for r in sorted(rows, key=lambda r: int(r["page"]))]


def image_records(report_id: str) -> dict[int, dict]:
    out = {}
    with open(R3 / "runs" / "images.jsonl", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            if r["report_id"] == report_id:
                out[int(r["page"])] = {"status": r["status"], "text": r["text"] if r["status"] == "transcribed" else ""}
    return out


def jev_page_lists() -> dict[tuple[str, str], str]:
    """Only the page list Jev was shown for each report and field. Nothing else is read from jev.jsonl."""
    out = {}
    with open(R3 / "runs" / "jev.jsonl", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            pages = json.loads(r["pages"]) if isinstance(r["pages"], str) else r["pages"]
            out[(r["report_id"], r["field"])] = ";".join(str(p) for p in pages)
    return out


def guide_sections() -> str:
    """The "The fields", "Item types", and "Amendments and contradictions" sections, verbatim."""
    text = (R3 / "REVIEW_GUIDE.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(?=## )", text)
    keep = [p for p in parts if p.startswith(("## The fields", "## Item types", "## Amendments and contradictions"))]
    assert len(keep) == 3, "review guide sections not found"
    return "\n".join(p.rstrip() + "\n" for p in keep)


def dev_reports() -> dict[str, str]:
    """round 3 report_id -> round 1 pilot id."""
    return {rid: v["read_before"][6:] for rid, v in versions().items() if v["read_before"].startswith("pilot:")}


def test_reports() -> dict[str, tuple[str, str]]:
    """round 3 report_id -> (gold set, gold report_id), excluding r2_pge_2017_2019."""
    out = {}
    for rid, v in versions().items():
        if v["read_before"].startswith("round2:"):
            r2 = v["read_before"][7:]
            if r2 not in EXCLUDED_TEST_REPORTS:
                out[rid] = ("round2", r2)
    for g in read_csv(R3 / "gold_sample10.csv"):
        out[g["report_id"]] = ("round3", g["report_id"])
    return out


def correction_files(report_id: str) -> list[Path]:
    urls = [u.strip() for u in versions()[report_id]["partial_corrections"].split(";") if u.strip()]
    files = []
    for url in urls:
        path = R3 / "raw_amend" / url.rsplit("/", 1)[-1]
        if not path.exists():
            raise FileNotFoundError(f"correction letter missing on disk: {path.name}")
        files.append(path)
    return files


def current_values(report_id: str) -> dict:
    """The event's current values for special items. Values only: no confidences, no sources."""
    row = next(r for r in read_csv(R3 / "dataset.csv") if r["report_id"] == report_id)
    keep = FIELDS + ["counties_deenergized_names", "event_duration_hours", "version"]
    return {k: row[k] for k in keep}


# ------------------------------------------------------------------ item sets

def is_special(item: dict) -> bool:
    return item["reason"] in SPECIAL_REASONS


def is_redline(item: dict) -> bool:
    return item["field"] == "numeric fields" and "redline" in item["question"]


def clean_item(raw: dict) -> dict:
    item = {k: raw.get(k, "") for k in ITEM_KEYS}
    assert not FORBIDDEN & set(item), "forbidden key in item"
    return item


def nine_field_items(report_id: str, queue_by_key: dict, jev_pages: dict) -> list[dict]:
    """Queue-style items for all nine fields, as asked on the development and test reports."""
    cat_text = {}
    for q in queue():
        if q["field"] in CAT:
            cat_text[q["field"]] = (q["question"], q["options"])
    items = []
    for field in CAT:
        q = queue_by_key.get((report_id, field))
        question, options = cat_text[field]
        items.append(clean_item({
            "item": field, "reason": q["reason"] if q else "", "field": field, "question": question, "options": options,
            "page_refs": q["page_refs"] if q else jev_pages.get((report_id, field), ""), "detail": q["detail"] if q else "",
        }))
    for field in NUM:
        question, fmt = NUMBER_QUESTIONS[field]
        items.append(clean_item({"item": field, "reason": "", "field": field, "question": question, "options": fmt, "page_refs": "", "detail": ""}))
    return items


def sessions(set_name: str) -> list[dict]:
    """Every reviewer session of a set: {session, report_id, items, special}."""
    q = queue()
    queue_by_key = {(x["report_id"], x["field"]): x for x in q if x["field"] in CAT}
    out = []
    if set_name in ("dev", "test"):
        jev_pages = jev_page_lists()
        reports = sorted(dev_reports() if set_name == "dev" else test_reports())
        for rid in reports:
            out.append({"session": f"{set_name}__{rid}", "report_id": rid, "special": False,
                        "items": nine_field_items(rid, queue_by_key, jev_pages)})
        if set_name == "dev":
            by_report: dict[str, list] = {}
            for x in q:
                if x["item"] in DEV_SPECIAL_ITEMS:
                    by_report.setdefault(x["report_id"], []).append(clean_item(x))
            for rid, items in sorted(by_report.items()):
                out.append({"session": f"dev__{rid}__special", "report_id": rid, "special": True, "items": items})
        return out
    if set_name == "queue":
        tests = test_reports()
        by_report: dict[str, dict[str, list]] = {}
        for x in q:
            if is_redline(x):
                continue  # sent straight to unresolved (DESIGN.md, changes before the freeze)
            if x["report_id"] in tests and x["field"] in CAT:
                continue  # settled by the test answer for the same report and field
            kind = "special" if is_special(x) else "blind"
            by_report.setdefault(x["report_id"], {"blind": [], "special": []})[kind].append(clean_item(x))
        for rid, kinds in sorted(by_report.items()):
            if kinds["blind"]:
                out.append({"session": f"queue__{rid}", "report_id": rid, "special": False, "items": kinds["blind"]})
            if kinds["special"]:
                out.append({"session": f"queue__{rid}__special", "report_id": rid, "special": True, "items": kinds["special"]})
        return out
    raise ValueError(set_name)


# ------------------------------------------------------------------ instructions

ANSWER_FORMAT = """\
## Your answer

Answer every item in `items.json`. Each answer is one JSON object:

| Key | What to put |
|---|---|
| `item` | The item's `item` value, exactly. |
| `value` | Your answer. For an item with `options`, exactly one of the listed options. For a number item, a whole number, a time as `YYYY-MM-DD HH:MM`, or `null`, as the item's `options` says. For a partial correction, redline, or unreadable-page item, `no change` or corrections as `field=value; field=value`, using the field names in `current_values.json`. For a workbook versus PDF time item, the time you decide is right, as `YYYY-MM-DD HH:MM`. |
| `pages` | The pages that support your answer, as a list of strings. A report page is its PDF page number (`"9"`). A correction letter page is prefixed with its folder (`"correction:3"`, `"correction2:1"`). |
| `quote` | A short passage copied exactly from one of the cited pages that supports your answer: one contiguous stretch of text, no ellipses, no paraphrase, copied character for character from the page file, including stray footnote numbers inside it. One sentence or one table row is safest. It may come from a page's text or its transcription. Leave it empty only when `value` is `not_stated` or `null`. |
| `search_terms` | The searches you ran for this item, as a list of strings. Required when `value` is `not_stated` or `null`; then `pages` lists the pages you read. |
| `note` | Anything a second reader needs. Start with `CONTRADICTION:` or `UNSURE:` as the guide describes. |
| `unsure` | `true` if the note starts with `UNSURE:`, otherwise `false`. |

Where the guide below says `decision_value`, `decision_pages`, or `notes`, use `value`, `pages`, and `note`. Where it says to compare with the event's row in `dataset.csv`, use `current_values.json`. An item with an empty `reason` is a routine check: answer its question the same way. Answer only from the report and any correction letter provided; don't use outside knowledge.
"""

CLAUDE_ACCESS = """\
## The files

Everything you may use is in this folder:

- `items.json`: the items to answer.
- `pages/NNN.txt`: the text layer of PDF page NNN of the report. Some pages also have `pages/NNN_transcription.txt`, a machine transcription of a table that was only an image.
- `pages/NNN.png`: a rendered image of the page, for pages with an image-only table, little text, or scrambled text, and for every page an item lists in `page_refs`.
- `correction/` and `correction2/` (only when an item needs them): the same files for a correction letter.
- `current_values.json` (only for partial correction, unreadable-page, and workbook items): the event's current values.

Use Grep to search the pages and Read to open them. When you are done, write your answers to `answers.json` in this folder as `{"answers": [ ... ]}` and nothing else.
"""

SOL_ACCESS = """\
## The report

You read the report through three tools. Each takes a `document`: `report` for the report, or `correction` and `correction2` for a correction letter when an item needs one.

- `search(query, document)`: the pages whose text contains the query (case-insensitive), with a short snippet.
- `read_page(n, document)`: the text layer of PDF page n, plus its stored transcription when a table on the page was only an image.
- `view_page(n, document)`: the rendered page image, for image-only tables and scrambled text.

When you are done, reply with the JSON object `{"answers": [ ... ]}` and nothing else.
"""


def instructions(reviewer: str) -> str:
    access = CLAUDE_ACCESS if reviewer == "claude" else SOL_ACCESS
    intro = (
        "# Review task\n\n"
        "You are settling items from one California utility PSPS post-event report. "
        "The rules below are the ones a human reviewer uses.\n\n"
    )
    return intro + access + "\n" + ANSWER_FORMAT + "\n# Review guide (excerpt)\n\n" + guide_sections()


# ------------------------------------------------------------------ packet folders

def _pdf_for(report_id: str) -> Path:
    return R3 / versions()[report_id]["document"]


def _render(pdf: Path, page: int, out: Path) -> None:
    import fitz

    if not out.exists():
        with fitz.open(pdf) as doc:
            doc[page - 1].get_pixmap(dpi=DPI).save(out)


def _needs_image(text: str) -> bool:
    return len(re.sub(r"\s", "", text)) < LOW_TEXT_CHARS or is_garbled(text)


def _write_document(folder: Path, texts: list[str], pdf: Path, images: dict[int, dict], refs: set[int]) -> dict:
    folder.mkdir(parents=True, exist_ok=True)
    listing = {"pages": len(texts), "png": [], "transcribed": []}
    for n, text in enumerate(texts, start=1):
        (folder / f"{n:03d}.txt").write_text(text, encoding="utf-8")
        rec = images.get(n)
        if rec and rec["text"]:
            (folder / f"{n:03d}_transcription.txt").write_text(rec["text"], encoding="utf-8")
            listing["transcribed"].append(n)
        if rec or n in refs or _needs_image(text):
            _render(pdf, n, folder / f"{n:03d}.png")
            listing["png"].append(n)
    return listing


def documents(report_id: str, items: list[dict]) -> dict[str, dict[int, dict]]:
    """The text a session's quotes are checked against: {document: {page: {text, transcription, image_only}}}.

    image_only marks a page with no transcription whose text layer is too thin or scrambled to check.
    """
    import fitz

    texts = report_pages(report_id)
    images = image_records(report_id)
    docs = {"report": {n: {"text": t, "transcription": (images.get(n) or {}).get("text", ""),
                           "image_only": _needs_image(t) and not (images.get(n) or {}).get("text")}
                       for n, t in enumerate(texts, start=1)}}
    if any(i["field"] == "all fields" for i in items):
        for k, pdf in enumerate(correction_files(report_id), start=1):
            with fitz.open(pdf) as doc:
                ctexts = [p.get_text() for p in doc]
            docs["correction" if k == 1 else f"correction{k}"] = {
                n: {"text": t, "transcription": "", "image_only": _needs_image(t)} for n, t in enumerate(ctexts, start=1)}
    return docs


def page_refs(items: list[dict]) -> set[int]:
    refs = set()
    for item in items:
        refs |= {int(p) for p in re.findall(r"\d+", item["page_refs"])}
    return refs


def build(session: dict, reviewer_root: Path) -> Path:
    """Write one session's packet. Returns the folder."""
    import shutil

    folder = reviewer_root / session["session"]
    if folder.exists():
        shutil.rmtree(folder)
    rid = session["report_id"]
    texts = report_pages(rid)
    refs = {r for r in page_refs(session["items"]) if 1 <= r <= len(texts)}
    manifest = {"session": session["session"], "report_id": rid, "documents": {}}
    manifest["documents"]["report"] = _write_document(folder / "pages", texts, _pdf_for(rid), image_records(rid), refs)
    if any(i["field"] == "all fields" for i in session["items"]):
        import fitz

        for k, pdf in enumerate(correction_files(rid), start=1):
            name = "correction" if k == 1 else f"correction{k}"
            with fitz.open(pdf) as doc:
                ctexts = [p.get_text() for p in doc]
            manifest["documents"][name] = _write_document(folder / name, ctexts, pdf, {}, set())
    if session["special"]:
        (folder / "current_values.json").write_text(json.dumps(current_values(rid), indent=2), encoding="utf-8")
    (folder / "items.json").write_text(json.dumps(session["items"], indent=2), encoding="utf-8")
    (folder / "instructions.md").write_text(instructions(reviewer_root.name), encoding="utf-8")
    assert_blind(folder)
    # The manifest stays outside the packet so a reviewer never reads it.
    (reviewer_root / f"{session['session']}.manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return folder


def assert_blind(folder: Path) -> None:
    """Fail loudly if a packet holds anything a reviewer must not see."""
    items = json.loads((folder / "items.json").read_text(encoding="utf-8"))
    for item in items:
        assert set(item) == set(ITEM_KEYS), f"unexpected item keys {set(item)}"
    allowed_top = {"items.json", "instructions.md", "pages", "correction", "correction2", "current_values.json"}
    extra = {p.name for p in folder.iterdir()} - allowed_top
    assert not extra, f"unexpected files in packet: {extra}"
    if (folder / "current_values.json").exists():
        assert all(i["reason"] in SPECIAL_REASONS for i in items), "current values next to a blind item"


def packet_dir(set_name: str, reviewer: str) -> Path:
    return PACKET_ROOT / set_name / reviewer


CODE_FILES = ["packets.py", "verify.py", "run_claude.py", "run_sol.py", "score.py", "apply.py"]


def file_sha256(path: Path) -> str:
    import hashlib

    # Hash with normalized line endings so a CRLF checkout does not look like a change.
    return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()


def frozen(set_name: str) -> dict | None:
    """For the test and queue sets: the freeze, after checking the code still matches it."""
    if set_name == "dev":
        return None
    path = HERE / "freeze.json"
    if not path.exists():
        raise SystemExit(f"STOP: no freeze.json; the {set_name} set runs only after the freeze")
    freeze = json.loads(path.read_text(encoding="utf-8"))
    changed = [f for f, h in freeze["code_sha256"].items() if file_sha256(HERE / f) != h]
    if changed:
        raise SystemExit(f"STOP: code changed since the freeze: {changed}")
    return freeze


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["build", "list"])
    ap.add_argument("--set", required=True, choices=["dev", "test", "queue"])
    ap.add_argument("--reviewer", default="claude", choices=["claude", "sol"])
    args = ap.parse_args()
    ss = sessions(args.set)
    if args.command == "list":
        for s in ss:
            print(s["session"], len(s["items"]), [i["item"] for i in s["items"]])
        print(len(ss), "sessions,", sum(len(s["items"]) for s in ss), "items")
        return
    root = packet_dir(args.set, args.reviewer)
    for s in ss:
        build(s, root)
    print(f"built {len(ss)} packets in {root}")


if __name__ == "__main__":
    main()

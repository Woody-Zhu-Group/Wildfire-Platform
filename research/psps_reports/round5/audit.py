"""Round 5: a human audit of the PSPS dataset (AUDIT_PLAN.md).

    python research/psps_reports/round5/audit.py sample   # sample.csv (committed before the sheet is built)
    python research/psps_reports/round5/audit.py build    # audit_sheet.xlsx, audit_key.csv (gitignored), key_sha256.txt
    python research/psps_reports/round5/audit.py score --sheet <filled .xlsx>   # audit_results.md, audit_scored.csv

The sheet never shows a model answer, dataset value, gold label, flag, or sample group; build() checks that.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import random
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
R3 = ROOT / "round3"
R4 = ROOT / "round4"
sys.path.insert(0, str(R4))

import packets as pk  # noqa: E402

SEED = 20260929
SIZES = {"queue": 30, "unflagged": 15, "old_test_label": 15}
SAMPLE_COLUMNS = ["audit_id", "group", "report_id", "field", "queue_item", "gold_set"]
SHEET = HERE / "audit_sheet.xlsx"
KEY = HERE / "audit_key.csv"
KEY_HASH = HERE / "key_sha256.txt"
KEY_COLUMNS = SAMPLE_COLUMNS + ["dataset_value", "dataset_review_method", "round3_value",
                                "round4_agreed_value", "old_gold", "old_gold_certain"]

OPTIONS = {
    "mbl_advance_notice": ["all_notified", "some_not_notified", "not_stated", "not_applicable"],
    "wind_threshold_cited": ["met", "not_met", "not_stated"],
    "complaints_reported": ["one_or_more", "zero", "not_stated"],
    "claims_reported": ["one_or_more", "zero", "not_stated"],
    "canceled_after_notice": ["yes", "no", "not_stated"],
}
NUMBER_FORMAT = {
    "customers_deenergized": "a whole number (0 if nobody was shut off), or not_stated",
    "counties_deenergized": "a whole number, or not_stated",
    "first_deenergization": "YYYY-MM-DD HH:MM (24-hour local time), or not_stated",
    "last_restoration": "YYYY-MM-DD HH:MM (24-hour local time), or not_stated",
}
SEARCH_WORDS = {
    "mbl_advance_notice": "Medical Baseline; MBL; critical care; notification",
    "wind_threshold_cited": "wind; gust; mph; threshold; criteria",
    "complaints_reported": "complaint",
    "claims_reported": "claim",
    "canceled_after_notice": "cancel; notified; scope; not de-energized",
    "customers_deenergized": "customers; de-energized; Table 1",
    "first_deenergization": "de-energized; circuit; time",
    "last_restoration": "restored; restoration; circuit",
    "counties_deenergized": "county; counties",
}
TIME_FIELDS = ("first_deenergization", "last_restoration")
SHEET_COLUMNS = [("ID", 7), ("Report", 38), ("PDF", 10), ("Workbook", 11), ("Question", 60),
                 ("Allowed answers", 30), ("Pages to start from", 19), ("Search words", 22),
                 ("My answer", 22), ("My page", 10), ("Note", 40)]
ANSWER_COL, PAGE_COL, NOTE_COL = 9, 10, 11  # 1-based columns of the auditor's cells


def read_csv(path: Path) -> list[dict]:
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def write_csv(path: Path, rows: list[dict], columns: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=columns)
        w.writeheader()
        w.writerows(rows)


# ------------------------------------------------------------------ sample

def populations() -> dict[str, dict[tuple[str, str], dict]]:
    """Each group's population: (report_id, field) -> source ids."""
    dataset = read_csv(R4 / "dataset_reviewed.csv")
    agreed_items = {(r["report_id"], r["field"]): r["item"] for r in read_csv(R4 / "reviewed_queue.csv")
                    if r["status"] == "model_review_agreed" and r["field"] in pk.FIELDS}
    queue, unflagged = {}, {}
    for row in dataset:
        for f in pk.FIELDS:
            key, method = (row["report_id"], f), row[f"{f}_review_method"]
            if method == "model_review_agreed":
                queue[key] = {"queue_item": agreed_items[key]}  # KeyError means an agreed field with no item
            elif method == "unflagged":
                unflagged[key] = {}
    old = {(r["report_id"], r["field"]): {"gold_set": r["gold_set"]}
           for r in read_csv(R4 / "test_gold.csv") if r["excluded"] == "no"}
    return {"queue": queue, "unflagged": unflagged, "old_test_label": old}


def cmd_sample() -> None:
    pops = populations()
    rng = random.Random(SEED)
    taken: set[tuple[str, str]] = set()
    rows = []
    for group, n in SIZES.items():
        keys = sorted(k for k in pops[group] if k not in taken)
        for key in rng.sample(keys, n):
            taken.add(key)
            rows.append({"group": group, "report_id": key[0], "field": key[1], "queue_item": "", "gold_set": "",
                         **pops[group][key]})
        print(f"{group}: {n} of {len(keys)}")
    rows.sort(key=lambda r: (r["report_id"], pk.FIELDS.index(r["field"])))
    for i, r in enumerate(rows, start=1):
        r["audit_id"] = f"A{i:02d}"
    write_csv(HERE / "sample.csv", rows, SAMPLE_COLUMNS)
    print(f"sample.csv: {len(rows)} rows on {len({r['report_id'] for r in rows})} reports")


# ------------------------------------------------------------------ build

def questions() -> dict[str, str]:
    out = {f: q for f, (q, _) in pk.NUMBER_QUESTIONS.items()}
    for q in pk.queue():
        if q["field"] in OPTIONS:
            out[q["field"]] = q["question"]
    return out


def start_pages() -> dict[str, str]:
    """report_id -> the report's summary and event-table pages (the same for every field of a report)."""
    out = {}
    with open(R3 / "runs" / "jev.jsonl", encoding="utf-8") as fh:
        for line in fh:
            r = json.loads(line)
            pages = json.loads(r["summary_pages"]) if isinstance(r["summary_pages"], str) else r["summary_pages"]
            out[r["report_id"]] = "; ".join(str(p) for p in pages) if pages else "none found: search the PDF"
    return out


def report_name(v: dict) -> str:
    name = f"{v['utility']}, {v['label']}"
    return name + (" (amended version)" if v["version"] != "original" else "")


def key_rows(sample: list[dict]) -> list[dict]:
    dataset = {r["report_id"]: r for r in read_csv(R4 / "dataset_reviewed.csv")}
    round3 = {r["report_id"]: r for r in read_csv(R3 / "dataset.csv")}
    queue = {r["item"]: r for r in read_csv(R4 / "reviewed_queue.csv")}
    gold = {(r["report_id"], r["field"]): r for r in read_csv(R4 / "test_gold.csv") if r["excluded"] == "no"}
    test = {(r["report_id"], r["field"]): r for r in read_csv(R4 / "runs" / "test_scored.csv")}
    out = []
    for s in sample:
        key, f = (s["report_id"], s["field"]), s["field"]
        agreed = ""
        if s["queue_item"]:
            agreed = queue[s["queue_item"]]["r1_canonical"]
        elif key in test and test[key]["status"] == "model_review_agreed":
            agreed = test[key]["r1_canonical"]
        g = gold.get(key, {})
        out.append({**{c: s[c] for c in SAMPLE_COLUMNS},
                    "dataset_value": dataset[s["report_id"]][f], "dataset_review_method": dataset[s["report_id"]][f"{f}_review_method"],
                    "round3_value": round3[s["report_id"]][f], "round4_agreed_value": agreed,
                    "old_gold": g.get("gold", ""), "old_gold_certain": g.get("certain", "")})
    return out


def assert_blind(visible: list[list[str]], keys: list[dict]) -> None:
    """No row's visible text may contain its own dataset value, old gold, or round 4 answer when that value
    is a time or a number of 3 or more digits (category words are the allowed answers, so they are not checked)."""
    for cells, k in zip(visible, keys):
        text = " ".join(cells)
        for v in (k["dataset_value"], k["old_gold"], k["round4_agreed_value"], k["round3_value"]):
            for part in v.split("|"):
                part = part.strip()
                if re.fullmatch(r"\d{4}-\d{2}-\d{2} \d{2}:\d{2}|\d{3,}", part):
                    hit = re.search(rf"(?<!\d){re.escape(part)}(?!\d)", text)
                    assert not hit, f"{k['audit_id']}: a value is visible in the sheet"
        assert k["group"] not in text, f"{k['audit_id']}: the group is visible"


def rules_lines() -> list[tuple[str, bool]]:
    """The guide's labeling rules as (text, is_heading) lines."""
    text = (R3 / "REVIEW_GUIDE.md").read_text(encoding="utf-8")
    parts = re.split(r"(?m)^(?=## )", text)
    keep = [p for p in parts if p.startswith(("## The fields", "## Amendments and contradictions"))]
    assert len(keep) == 2, "review guide sections not found"
    lines = []
    for line in "\n".join(keep).splitlines():
        line = line.replace("`", "").replace("**", "")
        if re.fullmatch(r"\|[-| ]+\|", line.strip()):
            continue
        if line.startswith("|"):
            line = "   ".join(c.strip() for c in line.strip().strip("|").split("|"))
        if "contradictions.csv" in line:
            continue  # a pipeline step, not a labeling rule
        line = (line.replace("decision_value", "My answer").replace("document_url", "the PDF link")
                .replace("write null", "write not_stated")
                .replace("pick the closest one, add", "pick the closest one (or write both as a|b), add"))
        lines.append((line.lstrip("# "), True) if line.startswith("#") else (line, False))
    return lines


HOW_TO = [
    "How to fill in this sheet",
    "",
    "1. Work down the Audit tab one report at a time. Rows are sorted by report, so open each PDF once and answer all of its rows.",
    "2. Page numbers are PDF page numbers, counted from the first page of the file, as your PDF viewer shows them.",
    "3. 'Pages to start from' are the report's summary and event-table pages. They are the same for every row of a report and are only a starting point. Use the search words to look through the whole PDF.",
    "4. Answer each question from the report alone, using the rules on the Rules tab.",
    "5. My answer: pick from the dropdown. For numbers and times, type the value (a whole number, or a time as YYYY-MM-DD HH:MM in 24-hour local time), or pick not_stated.",
    "6. If the report states two values and the rules on the Rules tab don't settle which one to use, you may write both as a|b (for example 36307|36037). Start the note with UNSURE: and say why.",
    "7. My page: the PDF page or pages that support your answer, separated by semicolons (for example 9;41). Write none if the report is silent.",
    "8. Note: a short quote or reason. Start it with UNSURE: if you are not sure, CONTRADICTION: if the report disagrees with itself, and SEEN: if you remember this value from FINDINGS.md or the PR.",
    "9. Workbook: for time rows of an event whose utility posted a data workbook, the link is given. Use its circuit table when it lists every circuit.",
    "10. Don't open sample.csv or audit_key.csv until you are done. Save the sheet and send it back.",
]


def cmd_build() -> None:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font, PatternFill
    from openpyxl.worksheet.datavalidation import DataValidation

    sample = read_csv(HERE / "sample.csv")
    versions = pk.versions()
    workbooks = {r["report_id"]: r["workbook_url"] for r in read_csv(R3 / "workbooks.csv")}
    qs, pages = questions(), start_pages()
    keys = key_rows(sample)

    wb = Workbook()
    ws = wb.active
    ws.title = "Audit"
    for c, (name, width) in enumerate(SHEET_COLUMNS, start=1):
        cell = ws.cell(row=1, column=c, value=name)
        cell.font = Font(bold=True)
        cell.fill = PatternFill("solid", fgColor="DDDDDD")
        ws.column_dimensions[cell.column_letter].width = width
    ws.freeze_panes = "B2"
    lists = {f: DataValidation(type="list", formula1='"' + ",".join(opts) + '"', allow_blank=True,
                               showErrorMessage=True, errorTitle="Not an allowed answer",
                               error="Pick one of the listed answers, or write two as a|b in the note.")
             for f, opts in OPTIONS.items()}
    number_list = DataValidation(type="list", formula1='"not_stated"', allow_blank=True, showErrorMessage=False,
                                 promptTitle="Number or time", prompt="Type the value, or pick not_stated.", showInputMessage=True)
    for dv in [*lists.values(), number_list]:
        ws.add_data_validation(dv)
    visible = []
    for i, s in enumerate(sample, start=2):
        v, f = versions[s["report_id"]], s["field"]
        workbook = workbooks.get(s["report_id"], "") if f in TIME_FIELDS else ""
        allowed = "; ".join(OPTIONS[f]) if f in OPTIONS else NUMBER_FORMAT[f]
        cells = [s["audit_id"], report_name(v), "Open PDF", "Open workbook" if workbook else "", qs[f], allowed,
                 pages[s["report_id"]], SEARCH_WORDS[f], "", "", ""]
        visible.append([str(c) for c in cells] + [v["document_url"], workbook])
        for c, value in enumerate(cells, start=1):
            cell = ws.cell(row=i, column=c, value=value)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
        ws.cell(row=i, column=3).hyperlink = v["document_url"]
        ws.cell(row=i, column=3).font = Font(color="0563C1", underline="single")
        if workbook:
            ws.cell(row=i, column=4).hyperlink = workbook
            ws.cell(row=i, column=4).font = Font(color="0563C1", underline="single")
        for c in (ANSWER_COL, PAGE_COL, NOTE_COL):
            ws.cell(row=i, column=c).number_format = "@"  # keep times and page lists as typed text
            ws.cell(row=i, column=c).fill = PatternFill("solid", fgColor="FFF7D6")
        (lists[f] if f in OPTIONS else number_list).add(ws.cell(row=i, column=ANSWER_COL))
    assert_blind(visible, keys)

    for title, lines in (("How to", [(t, i == 0) for i, t in enumerate(HOW_TO)]), ("Rules", rules_lines())):
        sheet = wb.create_sheet(title)
        sheet.column_dimensions["A"].width = 130
        for r, (line, heading) in enumerate(lines, start=1):
            cell = sheet.cell(row=r, column=1, value=line)
            cell.alignment = Alignment(wrap_text=True, vertical="top")
            # Excel does not grow wrapped rows written by openpyxl, so set the height (about 95 characters a line).
            sheet.row_dimensions[r].height = 15 * max(1, math.ceil(len(line) / 95))
            if heading:
                cell.font = Font(bold=True)
    wb.move_sheet("How to", offset=-1)
    wb.active = wb.sheetnames.index("Audit")
    wb.save(SHEET)

    write_csv(KEY, keys, KEY_COLUMNS)
    KEY_HASH.write_text(f"{pk.file_sha256(KEY)}  audit_key.csv\n", encoding="utf-8")
    print(f"audit_sheet.xlsx: {len(sample)} rows on {len({s['report_id'] for s in sample})} reports; "
          f"audit_key.csv written (gitignored), SHA-256 in key_sha256.txt")


# ------------------------------------------------------------------ score

NOT_STATED = {"", "null", "none", "not_stated", "not stated", "notstated"}


def canon(field: str, value) -> str | None:
    """Canonical form of one answer, or None if it cannot be read."""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return str(int(value)) if float(value).is_integer() else None
    v = str(value).strip()
    if v.lower() in NOT_STATED:
        return "not_stated"
    if field in OPTIONS:
        v = v.lower().replace(" ", "_")
        return v if v in OPTIONS[field] else None
    if field in TIME_FIELDS:
        for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%m/%d/%Y %H:%M"):
            try:
                return datetime.strptime(v, fmt).strftime("%Y-%m-%d %H:%M")
            except ValueError:
                pass
        return None
    v = v.replace(",", "")
    return str(int(v)) if re.fullmatch(r"\d+", v) else None


def values(field: str, value) -> set[str] | None:
    """All canonical values in an answer ('a|b' gives two). None if any part cannot be read."""
    if value is None:
        return set()
    parts = str(value).split("|") if isinstance(value, str) and "|" in value else [value]
    out = {canon(field, p) for p in parts}
    return None if None in out else out


def wilson(k: int, n: int, z: float = 1.959964) -> tuple[float, float]:
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (c - h, c + h)


def fmt(k: int, n: int) -> str:
    if n == 0:
        return "no rows"
    lo, hi = wilson(k, n)
    return f"{k}/{n} ({100 * k / n:.1f}%, 95% CI {100 * lo:.1f} to {100 * hi:.1f})"


def read_answers(path: Path) -> dict[str, dict]:
    from openpyxl import load_workbook

    ws = load_workbook(path, data_only=True)["Audit"]
    header = [c.value for c in ws[1]]
    assert header == [name for name, _ in SHEET_COLUMNS], "unexpected sheet columns"
    out = {}
    for row in ws.iter_rows(min_row=2, values_only=True):
        if row[0]:
            out[row[0]] = {"answer": row[ANSWER_COL - 1], "page": row[PAGE_COL - 1], "note": row[NOTE_COL - 1] or ""}
    return out


def cmd_score(sheet: Path) -> None:
    if not KEY.exists():
        raise SystemExit("STOP: audit_key.csv is missing; run `audit.py build` only if the sheet was built from the same commit")
    expected = KEY_HASH.read_text(encoding="utf-8").split()[0]
    if pk.file_sha256(KEY) != expected:
        raise SystemExit("STOP: audit_key.csv does not match key_sha256.txt")
    keys = read_csv(KEY)
    answers = read_answers(sheet)
    rows, unreadable = [], []
    for k in keys:
        a = answers.get(k["audit_id"], {"answer": None, "page": None, "note": ""})
        human = values(k["field"], a["answer"])
        note = str(a["note"]).strip()
        row = {**k, "auditor_answer": "" if a["answer"] is None else str(a["answer"]), "auditor_page": a["page"] or "",
               "auditor_note": note, "unsure": note.upper().startswith("UNSURE:"), "seen": note.upper().startswith("SEEN:")}
        if human is None:
            unreadable.append(k["audit_id"])
            human = set()
        row["answered"] = bool(human)
        for name, col in (("dataset", "dataset_value"), ("old_gold", "old_gold"), ("round4", "round4_agreed_value")):
            compared = values(k["field"], k[col]) if k[col] else set()
            if compared is None:
                compared = {f"unreadable:{k[col]}"}  # a placeholder such as NO_MATCHING_PAGE never agrees
            if name == "dataset" and not k[col]:
                compared = {"not_stated"}  # an empty dataset value means the pipeline found no value
            row[f"{name}_agrees"] = bool(human and compared and human & compared) if human and compared else ""
        rows.append(row)
    cols = KEY_COLUMNS + ["auditor_answer", "auditor_page", "auditor_note", "unsure", "seen", "answered",
                          "dataset_agrees", "old_gold_agrees", "round4_agrees"]
    write_csv(HERE / "audit_scored.csv", rows, cols)

    def line(label: str, sel: list[dict], col: str) -> str:
        base = [r for r in sel if r["answered"] and r[col] != ""]
        parts = [fmt(sum(r[col] for r in base), len(base))]
        for flag in ("unsure", "seen"):
            sub = [r for r in base if not r[flag]]
            parts.append(fmt(sum(r[col] for r in sub), len(sub)))
        return f"| {label} | " + " | ".join(parts) + " |"

    by = {g: [r for r in rows if r["group"] == g] for g in SIZES}
    out = ["# Round 5: human audit results", "",
           f"Sheet: `{sheet.name}`. Plan: `AUDIT_PLAN.md`. Scored by `audit.py score`; row-level results in `audit_scored.csv`.", "",
           f"- Rows answered: {sum(r['answered'] for r in rows)} of {len(rows)}; blank: "
           f"{', '.join(r['audit_id'] for r in rows if not r['answered'] and r['audit_id'] not in unreadable) or 'none'}; "
           f"unreadable answers (left out): {', '.join(unreadable) or 'none'}.",
           f"- Marked UNSURE: {sum(r['unsure'] for r in rows)}; marked SEEN: {sum(r['seen'] for r in rows)}.", "",
           "| Compared with the auditor | All answered rows | Without UNSURE | Without SEEN |", "|---|---|---|---|",
           line("Queue: values the two round 4 models agreed on", by["queue"], "dataset_agrees"),
           line("Unflagged: the pipeline's values", by["unflagged"], "dataset_agrees"),
           line("Old test labels (model-written gold)", by["old_test_label"], "old_gold_agrees"),
           line("Old test label rows: round 4 agreed answers, where both reviewers agreed", by["old_test_label"], "round4_agrees"),
           "", "## Disagreements", "",
           "| ID | Group | Report | Field | Auditor | Compared value | Note |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        col = "old_gold" if r["group"] == "old_test_label" else "dataset_value"
        agrees = r["old_gold_agrees"] if r["group"] == "old_test_label" else r["dataset_agrees"]
        if r["answered"] and agrees is False:
            out.append(f"| {r['audit_id']} | {r['group']} | {r['report_id']} | {r['field']} | {r['auditor_answer']} | "
                       f"{r[col] or '(empty)'} | {r['auditor_note'][:120].replace('|', '/')} |")
    (HERE / "audit_results.md").write_text("\n".join(out) + "\n", encoding="utf-8")
    print("\n".join(out[:14]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["sample", "build", "score"])
    ap.add_argument("--sheet", type=Path, default=SHEET)
    args = ap.parse_args()
    if args.command == "score":
        cmd_score(args.sheet)
    else:
        {"sample": cmd_sample, "build": cmd_build}[args.command]()


if __name__ == "__main__":
    main()

"""Shared helpers for the PSPS post-event report pilot.

Keys are read from the repo .env into the process environment and are never
printed or written anywhere.
"""

from __future__ import annotations

import csv
import json
import os
import re
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent

# Prices in USD per input or output token.
JEV_PRICE_IN = 0.042 / 1_000_000
LUNA_MODEL = "openai/gpt-6-luna"
LUNA_PRICE_IN = 0.10 / 1_000_000
LUNA_PRICE_OUT = 0.50 / 1_000_000
SPEND_CAP_USD = 2.0


def load_env() -> None:
    env_path = REPO / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" not in line or line.lstrip().startswith("#"):
            continue
        name, value = line.split("=", 1)
        name = name.strip()
        if name in ("TYPESAFE_API_KEY", "OPENROUTER_API_KEY") and name not in os.environ:
            os.environ[name] = value.strip().strip('"').strip("'")


def load_sources() -> list[dict]:
    with open(HERE / "sources.csv", newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_pages(report_id: str) -> list[dict]:
    path = HERE / "pages" / f"{report_id}.jsonl"
    with open(path, encoding="utf-8") as fh:
        return [json.loads(line) for line in fh]


def is_garbled(text: str) -> bool:
    """True when a page has words but almost no common English function words.

    Catches broken font encodings (for example letters shifted by 29 code
    points) and pure number tables.
    """
    words = re.findall(r"[A-Za-z]+", text)
    if len(words) <= 30:
        return False
    common = sum(1 for w in words if w.lower() in ("the", "and", "of", "to", "in", "for"))
    return common / len(words) < 0.01


def format_pages(pages: list[dict], max_chars_per_page: int | None = None) -> str:
    parts = []
    for page in pages:
        text = page["text"]
        if max_chars_per_page is not None:
            text = text[:max_chars_per_page]
        parts.append(f"[Page {page['page']}]\n{text.strip()}")
    return "\n\n".join(parts)


def normalize(text: str) -> str:
    text = text.replace("‐", "-").replace("‑", "-").replace("–", "-")
    text = text.replace("’", "'").replace("�", "'")
    return re.sub(r"\s+", " ", text).strip().lower()

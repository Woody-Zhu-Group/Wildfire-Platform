"""Reviewer 2: GPT-6 Sol on OpenRouter, with tools implemented in code.

Sol reads its own packet folder (same material as Reviewer 1's) only through
search, read_page, and view_page. Prompts are built in code from items that never
carry Jev's answer or confidence.

Spend: every call is recorded in runs/spend.json. The run stops before a call when
Sol's spend has reached the frozen cap (freeze.json) or, before the freeze, the
OpenRouter allowance.

    python research/psps_reports/round4/run_sol.py ping
    python research/psps_reports/round4/run_sol.py run --set dev [--only SESSION] [--workers 3]
"""

from __future__ import annotations

import argparse
import base64
import concurrent.futures as cf
import json
import os
import re
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import packets as pk

sys.path.insert(0, str(pk.ROOT))
from common import load_env  # noqa: E402

HERE = pk.HERE
RUNS = HERE / "runs"
MODEL = "openai/gpt-6-sol"
ALLOWANCE_USD = 25.0  # raised from 6.92 to 20 and then 25 on 2026-09-28 by the user
MAX_TOOL_CALLS = 50
MAX_ROUNDS = 30
REASONING_EFFORT = "medium"
_lock = threading.Lock()

ANSWER_SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["answers"],
    "properties": {"answers": {"type": "array", "items": {
        "type": "object", "additionalProperties": False,
        "required": ["item", "value", "pages", "quote", "search_terms", "note", "unsure"],
        "properties": {
            "item": {"type": "string"}, "value": {"type": "string"},
            "pages": {"type": "array", "items": {"type": "string"}}, "quote": {"type": "string"},
            "search_terms": {"type": "array", "items": {"type": "string"}}, "note": {"type": "string"},
            "unsure": {"type": "boolean"},
        }}}},
}


def tool_specs(documents: list[str]) -> list[dict]:
    doc = {"type": "string", "enum": documents, "description": "report, or a correction letter folder"}
    return [
        {"type": "function", "function": {"name": "search", "description": "Pages whose text contains the query (case-insensitive), with a snippet.",
         "parameters": {"type": "object", "additionalProperties": False, "required": ["query", "document"],
                        "properties": {"query": {"type": "string"}, "document": doc}}}},
        {"type": "function", "function": {"name": "read_page", "description": "The text layer of PDF page n, plus the stored transcription when there is one.",
         "parameters": {"type": "object", "additionalProperties": False, "required": ["n", "document"],
                        "properties": {"n": {"type": "integer"}, "document": doc}}}},
        {"type": "function", "function": {"name": "view_page", "description": "The rendered page image, for image-only tables and scrambled text.",
         "parameters": {"type": "object", "additionalProperties": False, "required": ["n", "document"],
                        "properties": {"n": {"type": "integer"}, "document": doc}}}},
    ]


# ------------------------------------------------------------------ spend

def _spend() -> dict:
    path = RUNS / "spend.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def sol_total() -> float:
    return sum(v for k, v in _spend().items() if k.startswith("sol_") and k.endswith("_usd") and isinstance(v, (int, float)))


def cap_usd() -> float:
    freeze = HERE / "freeze.json"
    if freeze.exists():
        return float(json.loads(freeze.read_text(encoding="utf-8"))["sol"]["cost_cap_usd"])
    return ALLOWANCE_USD


def add_spend(kind: str, usd: float) -> None:
    with _lock:
        state = _spend()
        state[kind] = round(state.get(kind, 0.0) + usd, 6)
        RUNS.mkdir(exist_ok=True)
        tmp = RUNS / "spend.json.tmp"
        tmp.write_text(json.dumps(state, indent=2), encoding="utf-8")
        tmp.replace(RUNS / "spend.json")


class CapReached(Exception):
    pass


class CreditError(Exception):
    pass


def openrouter(body: dict, kind: str) -> dict:
    with _lock:
        total = sol_total()
    if total >= min(cap_usd(), ALLOWANCE_USD):
        raise CapReached(f"Sol spend ${total:.4f} reached the cap ${min(cap_usd(), ALLOWANCE_USD):.2f}")
    body = {**body, "model": MODEL, "usage": {"include": True}}
    request = urllib.request.Request(
        "https://openrouter.ai/api/v1/chat/completions", data=json.dumps(body).encode("utf-8"),
        headers={"Authorization": f"Bearer {os.environ['OPENROUTER_API_KEY']}", "Content-Type": "application/json"})
    for attempt in range(4):
        try:
            with urllib.request.urlopen(request, timeout=900) as response:
                payload = json.load(response)
            break
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "replace")[:400]
            if exc.code == 402 or "credit" in detail.lower():
                raise CreditError(f"HTTP {exc.code}: {detail}") from None
            if attempt == 3 or exc.code in (400, 401, 403):
                raise RuntimeError(f"HTTP {exc.code}: {detail}") from None
            time.sleep(5 * (attempt + 1))
        except Exception:  # noqa: BLE001
            if attempt == 3:
                raise
            time.sleep(5 * (attempt + 1))
    usage = payload.get("usage") or {}
    add_spend(kind, float(usage.get("cost") or 0.0))
    if "error" in payload and "choices" not in payload:
        msg = json.dumps(payload["error"])[:400]
        if "credit" in msg.lower() or "402" in msg:
            raise CreditError(msg)
        raise RuntimeError(msg)
    return payload


# ------------------------------------------------------------------ tools over the packet folder

class Packet:
    def __init__(self, folder: Path):
        self.folder = folder
        self.docs = {"report": folder / "pages"}
        for name in ("correction", "correction2"):
            if (folder / name).exists():
                self.docs[name] = folder / name

    def pages(self, doc: str) -> int:
        return len([p for p in self.docs[doc].glob("[0-9][0-9][0-9].txt")])

    def _text(self, doc: str, n: int) -> tuple[str, str]:
        base = self.docs[doc]
        text = (base / f"{n:03d}.txt").read_text(encoding="utf-8")
        tr = base / f"{n:03d}_transcription.txt"
        return text, tr.read_text(encoding="utf-8") if tr.exists() else ""

    def search(self, query: str, doc: str) -> str:
        q = re.sub(r"\s+", " ", query).strip().casefold()
        if not q:
            return "Empty query."
        hits = []
        for n in range(1, self.pages(doc) + 1):
            text, tr = self._text(doc, n)
            for source in (text, tr):
                flat = re.sub(r"\s+", " ", source)
                i = flat.casefold().find(q)
                if i >= 0:
                    hits.append(f"page {n}: ...{flat[max(0, i - 100): i + len(q) + 100]}...")
                    break
        if not hits:
            return f"No page of {doc} contains '{query}'."
        more = f"\n({len(hits) - 30} more pages not shown; refine the query.)" if len(hits) > 30 else ""
        return f"{len(hits)} pages of {doc} contain '{query}':\n" + "\n".join(hits[:30]) + more

    def read_page(self, n: int, doc: str) -> str:
        if not 1 <= n <= self.pages(doc):
            return f"{doc} has pages 1 to {self.pages(doc)}."
        text, tr = self._text(doc, n)
        out = f"[{doc} page {n}, text layer]\n{text}"
        if tr:
            out += f"\n[{doc} page {n}, stored transcription of an image table]\n{tr}"
        return out

    def view_page(self, n: int, doc: str) -> tuple[str, str | None]:
        png = self.docs[doc] / f"{n:03d}.png"
        if not png.exists():
            return (f"No image of {doc} page {n}: its text layer is readable, so use read_page.", None)
        return (f"The image of {doc} page {n} follows in the next message.", base64.b64encode(png.read_bytes()).decode())


# ------------------------------------------------------------------ sessions

def first_message(session: dict, folder: Path, packet: Packet) -> str:
    docs = ", ".join(f"{d} ({packet.pages(d)} pages)" for d in packet.docs)
    parts = [f"Documents: {docs}.", "items.json:", json.dumps(session["items"], indent=2)]
    if (folder / "current_values.json").exists():
        parts += ["current_values.json:", (folder / "current_values.json").read_text(encoding="utf-8")]
    return "\n\n".join(parts)


def run_session(session: dict, folder: Path, max_tool_calls: int) -> dict:
    packet = Packet(folder)
    for item in session["items"]:
        assert not pk.FORBIDDEN & set(item)
    messages = [{"role": "system", "content": (folder / "instructions.md").read_text(encoding="utf-8")},
                {"role": "user", "content": first_message(session, folder, packet)}]
    tools = tool_specs(list(packet.docs))
    calls, rounds, cost, tokens = 0, 0, 0.0, {"prompt": 0, "completion": 0}
    log = []
    while True:
        rounds += 1
        body = {"messages": messages, "tools": tools, "reasoning": {"effort": REASONING_EFFORT},
                "response_format": {"type": "json_schema", "json_schema": {"name": "answers", "strict": True, "schema": ANSWER_SCHEMA}}}
        if calls >= max_tool_calls or rounds >= MAX_ROUNDS:
            body["tool_choice"] = "none"
        payload = openrouter(body, "sol_usd")
        usage = payload.get("usage") or {}
        cost += float(usage.get("cost") or 0.0)
        tokens["prompt"] += usage.get("prompt_tokens", 0)
        tokens["completion"] += usage.get("completion_tokens", 0)
        msg = payload["choices"][0]["message"]
        tool_calls = msg.get("tool_calls") or []
        if not tool_calls or body.get("tool_choice") == "none":
            content = msg.get("content") or ""
            try:
                answers = json.loads(content)["answers"]
                error = None
            except Exception as exc:  # noqa: BLE001
                answers, error = None, f"{exc!r}: {content[:300]}"
            return {"answers": answers, "parse_error": error, "tool_calls": calls, "rounds": rounds, "cost_usd": round(cost, 6),
                    "tokens": tokens, "model": payload.get("model"), "tool_log": log}
        messages.append({"role": "assistant", "content": msg.get("content") or "", "tool_calls": tool_calls})
        images = []
        for tc in tool_calls:
            calls += 1
            name = tc["function"]["name"]
            try:
                a = json.loads(tc["function"]["arguments"] or "{}")
                doc = a.get("document", "report")
                if doc not in packet.docs:
                    result = f"Unknown document {doc}; use one of {list(packet.docs)}."
                elif calls > max_tool_calls:
                    result = "Tool-call limit reached. Give your final answers now."
                elif name == "search":
                    result = packet.search(str(a.get("query", "")), doc)
                elif name == "read_page":
                    result = packet.read_page(int(a["n"]), doc)
                elif name == "view_page":
                    result, img = packet.view_page(int(a["n"]), doc)
                    if img:
                        images.append((doc, int(a["n"]), img))
                else:
                    result = f"Unknown tool {name}."
            except Exception as exc:  # noqa: BLE001
                result = f"Tool error: {exc}"
            log.append({"tool": name, "args": tc["function"]["arguments"][:200]})
            messages.append({"role": "tool", "tool_call_id": tc["id"], "content": result})
        if images:
            content = []
            for doc, n, img in images:
                content += [{"type": "text", "text": f"Image of {doc} page {n}:"},
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{img}"}}]
            messages.append({"role": "user", "content": content})
        if calls >= max_tool_calls:
            messages.append({"role": "user", "content": "Tool-call limit reached. Give your final answers now."})


def ping() -> None:
    load_env()
    try:
        payload = openrouter({"messages": [{"role": "user", "content": "Reply with the single word OK."}], "max_tokens": 16}, "sol_ping_usd")
    except CreditError as exc:
        raise SystemExit(f"STOP: OpenRouter credit error on the minimal Sol call: {exc}")
    usage = payload.get("usage") or {}
    record = {"at": time.strftime("%Y-%m-%d %H:%M"), "model": payload.get("model"), "reply": (payload["choices"][0]["message"].get("content") or "")[:40],
              "cost_usd": usage.get("cost"), "prompt_tokens": usage.get("prompt_tokens"), "completion_tokens": usage.get("completion_tokens")}
    (RUNS / "sol_ping.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(json.dumps(record))


def run_set(set_name: str, only: list[str] | None, workers: int, max_tool_calls: int, tag: str) -> None:
    load_env()
    freeze = pk.frozen(set_name)
    if freeze:
        max_tool_calls, tag = freeze["sol"]["max_tool_calls"], ""
    out_path = RUNS / f"sol_{set_name}{tag}.jsonl"
    done = set()
    if out_path.exists():
        done = {json.loads(line)["session"] for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    sessions = [s for s in pk.sessions(set_name) if (not only or s["session"] in only) and s["session"] not in done]
    root = pk.packet_dir(set_name, "sol")
    stop = threading.Event()
    stop_reason = []

    def one(session: dict) -> None:
        if stop.is_set():
            return
        folder = pk.build(session, root)
        start = time.time()
        try:
            result = run_session(session, folder, max_tool_calls)
        except (CapReached, CreditError) as exc:
            stop.set()
            stop_reason.append(f"{session['session']}: {exc}")
            return
        record = {"session": session["session"], "report_id": session["report_id"], "set": set_name,
                  "items": [i["item"] for i in session["items"]], "seconds": round(time.time() - start, 1), **result}
        with _lock:
            with open(out_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        print(f"{session['session']}: {result['tool_calls']} tool calls, ${result['cost_usd']:.4f}, "
              f"{'ok' if result['answers'] is not None else 'NO ANSWERS'}; Sol total ${sol_total():.4f}", flush=True)

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, sessions))
    if stop.is_set():
        with _lock:
            state = _spend()
            state.setdefault("sol_stops", []).append({"set": set_name, "at": time.strftime("%Y-%m-%d %H:%M"), "reason": stop_reason})
            (RUNS / "spend.json").write_text(json.dumps(state, indent=2), encoding="utf-8")
        raise SystemExit("STOPPED: " + "; ".join(stop_reason))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["ping", "run"])
    ap.add_argument("--set", choices=["dev", "test", "queue"])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-tool-calls", type=int, default=MAX_TOOL_CALLS)
    ap.add_argument("--tag", default="")
    args = ap.parse_args()
    if args.command == "ping":
        ping()
    else:
        run_set(args.set, args.only, args.workers, args.max_tool_calls, args.tag)


if __name__ == "__main__":
    main()

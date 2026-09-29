"""Reviewer 1: fresh headless Claude Code sessions (Claude Opus 5.5), one per packet.

Each session runs in its packet folder outside the repository with only the Read,
Grep, Glob, and Write tools; file access is confined to the folder, Write only to
answers.json; no MCP servers, no user or project settings, permission mode dontAsk.
The session that builds this round never answers items.

    python research/psps_reports/round4/run_claude.py isolation
    python research/psps_reports/round4/run_claude.py run --set dev [--only SESSION] [--workers 3]
"""

from __future__ import annotations

import argparse
import concurrent.futures as cf
import json
import os
import secrets
import shutil
import subprocess
import threading
import time
from pathlib import Path

import packets as pk

HERE = pk.HERE
RUNS = HERE / "runs"
MAX_TURNS = 80
SESSION_TIMEOUT_S = 3600
PROMPT = (
    "Read instructions.md in this folder and follow it exactly. Answer every item in items.json "
    "from the report files in this folder, then write answers.json in this folder."
)
_lock = threading.Lock()


def write_rule(folder: Path) -> str:
    """Write allowed only to answers.json in the packet. Claude Code matches rules on the
    POSIX form of Windows paths (C:/x -> /c/x); "//" marks an absolute path."""
    posix = folder.resolve().as_posix()
    if len(posix) > 1 and posix[1] == ":":
        posix = "/" + posix[0].lower() + posix[2:]
    # File-writing permissions are Edit rules; an Edit rule covers the Write tool.
    return f"Edit(/{posix}/answers.json)"


def claude_command(folder: Path, max_turns: int = MAX_TURNS) -> list[str]:
    return [
        shutil.which("claude") or "claude", "-p", "--model", "opus",
        "--tools", "Read,Grep,Glob,Write",
        "--allowedTools", "Read(./**)", "Grep(./**)", "Glob(./**)", write_rule(folder),
        "--permission-mode", "dontAsk",
        "--restricted", "--safe-mode", "--strict-mcp-config",
        "--setting-sources", "local",
        "--no-session-persistence",
        "--max-turns", str(max_turns),
        "--output-format", "stream-json", "--verbose",
    ]


def child_env() -> dict:
    env = dict(os.environ)
    # Subscription auth, not an API key; no secrets or parent-session markers in the child.
    for name in ("ANTHROPIC_API_KEY", "OPENROUTER_API_KEY", "TYPESAFE_API_KEY", "CLAUDECODE", "CLAUDE_CODE_ENTRYPOINT"):
        env.pop(name, None)
    return env


def tool_uses(transcript: Path) -> list[dict]:
    out = []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            e = json.loads(line)
        except json.JSONDecodeError:
            continue
        if e.get("type") == "assistant":
            out += [{"name": b["name"], "input": b["input"]} for b in e["message"].get("content", []) if b.get("type") == "tool_use"]
    return out


def run_session(folder: Path, prompt: str, max_turns: int, transcript: Path) -> dict:
    """Run one headless session in `folder`. Returns a summary of the stream."""
    start = time.time()
    with open(transcript, "w", encoding="utf-8") as out:
        # The prompt goes on stdin: a multi-line argument does not survive the Windows claude.CMD shim.
        proc = subprocess.run(claude_command(folder, max_turns), cwd=folder, env=child_env(), stdout=out, input=prompt,
                              stderr=subprocess.PIPE, timeout=SESSION_TIMEOUT_S, text=True, encoding="utf-8")
    events = []
    for line in transcript.read_text(encoding="utf-8").splitlines():
        try:
            events.append(json.loads(line))
        except json.JSONDecodeError:
            pass
    init = next((e for e in events if e.get("type") == "system" and e.get("subtype") == "init"), {})
    result = next((e for e in reversed(events) if e.get("type") == "result"), {})
    tool_calls: dict[str, int] = {}
    for e in events:
        if e.get("type") == "assistant":
            for block in e.get("message", {}).get("content", []):
                if block.get("type") == "tool_use":
                    tool_calls[block["name"]] = tool_calls.get(block["name"], 0) + 1
    return {
        "exit_code": proc.returncode, "stderr": proc.stderr[-2000:], "seconds": round(time.time() - start, 1),
        "model": init.get("model"), "tools": init.get("tools"), "mcp_servers": init.get("mcp_servers"),
        "permission_mode": init.get("permissionMode"), "claude_code_version": init.get("claude_code_version"),
        "num_turns": result.get("num_turns"), "subtype": result.get("subtype"), "is_error": result.get("is_error"),
        "result_text": (result.get("result") or "")[-2000:], "usage": result.get("usage"),
        "model_usage": result.get("modelUsage"), "notional_cost_usd": result.get("total_cost_usd"),
        "permission_denials": result.get("permission_denials"), "tool_calls": tool_calls,
    }


# ------------------------------------------------------------------ isolation check

def isolation() -> bool:
    """A session in a packet folder must not read, search, list, or write anything outside it."""
    base = pk.PACKET_ROOT / "isolation"
    if base.exists():
        shutil.rmtree(base)
    packet = base / "packet"
    (packet / "pages").mkdir(parents=True)
    inside, outside, sibling = (secrets.token_hex(8) for _ in range(3))
    (packet / "pages" / "001.txt").write_text(f"Reference code: IN-{inside}\n", encoding="utf-8")
    (base / "shared").mkdir()
    (base / "shared" / "notes.txt").write_text(f"Reference code: OUT-{outside}\n", encoding="utf-8")
    (base / "other_packet").mkdir()
    (base / "other_packet" / "answers.json").write_text(f'{{"reference": "SIB-{sibling}"}}', encoding="utf-8")
    repo_file = pk.ROOT / "README.md"
    repo_marker = repo_file.read_text(encoding="utf-8").splitlines()[0].strip()
    gold_marker = pk.read_csv(pk.ROOT / "gold_labels.csv")[0]["note"][:40]
    targets = {
        "outside file": base / "shared" / "notes.txt",
        "sibling packet": base / "other_packet" / "answers.json",
        "repository file": repo_file,
        "search outside": base,
        "write outside": base / "shared" / "draft.txt",
    }
    # Neutral wording: an earlier draft framed as an access test made the session decline
    # every step, which tests the model's manners, not the permission layer.
    prompt = f"""I'm setting up this folder for a document review. Please do each step below with your file tools and tell me the result of each one, including any error message.
1. Read pages/001.txt and quote it.
2. Read {targets['outside file']} and quote it.
3. Read {targets['sibling packet']} and quote it.
4. Read {targets['repository file']} and quote its first line.
5. Search for the text "Reference code" in the folder {targets['search outside']} and list the matching files.
6. Find all files in the folder {targets['search outside']}.
7. Create {targets['write outside']} containing the word draft.
8. Create pages/extra.txt in the current folder containing the word extra.
9. Create answers.json in the current folder containing {{"answers": []}}.
End with a short table of each step and whether it worked."""
    transcript = base / "transcript.jsonl"
    summary = run_session(packet, prompt, 30, transcript)
    raw = transcript.read_text(encoding="utf-8")
    attempted = json.dumps([u for u in tool_uses(transcript)]).replace("\\\\", "\\").lower()

    def tried(path: Path) -> bool:
        return path.name.lower() in attempted
    checks = {
        "session ran": summary["subtype"] is not None,
        "model is Opus": "opus" in str(summary["model"]).lower(),
        "only Read Grep Glob Write available": sorted(summary["tools"] or []) == ["Glob", "Grep", "Read", "Write"],
        "no MCP servers": not summary["mcp_servers"],
        "inside file readable": f"IN-{inside}" in raw,
        "answers.json writable": (packet / "answers.json").exists(),
        "outside reads were attempted": all(tried(targets[k]) for k in ("outside file", "sibling packet", "repository file", "write outside")),
        "outside file not read": f"OUT-{outside}" not in raw,
        "sibling packet not read": f"SIB-{sibling}" not in raw,
        "repository file not read": repo_marker not in raw,
        "gold labels not read": gold_marker not in raw,
        "no write outside answers.json": not (base / "shared" / "draft.txt").exists() and not (packet / "pages" / "extra.txt").exists(),
    }
    report = {"checks": checks, "passed": all(checks.values()), "summary": {k: summary[k] for k in
              ("model", "tools", "mcp_servers", "permission_mode", "claude_code_version", "num_turns", "permission_denials", "tool_calls")},
              "command": claude_command(packet, 30), "tool_uses": tool_uses(transcript), "final_text": summary["result_text"]}
    RUNS.mkdir(exist_ok=True)
    (RUNS / "isolation_check.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    for name, ok in checks.items():
        print(("PASS " if ok else "FAIL ") + name)
    print("isolation", "PASSED" if report["passed"] else "FAILED")
    return report["passed"]


# ------------------------------------------------------------------ runs

def run_set(set_name: str, only: list[str] | None, workers: int, max_turns: int, tag: str) -> None:
    if not json.loads((RUNS / "isolation_check.json").read_text(encoding="utf-8"))["passed"]:
        raise SystemExit("STOP: the packet isolation check has not passed")
    out_path = RUNS / f"claude_{set_name}{tag}.jsonl"
    done = set()
    if out_path.exists():
        done = {json.loads(line)["session"] for line in out_path.read_text(encoding="utf-8").splitlines() if line.strip()}
    sessions = [s for s in pk.sessions(set_name) if (not only or s["session"] in only) and s["session"] not in done]
    root = pk.packet_dir(set_name, "claude")
    transcripts = pk.PACKET_ROOT / "transcripts" / f"claude_{set_name}{tag}"
    transcripts.mkdir(parents=True, exist_ok=True)
    stop = threading.Event()

    def one(session: dict) -> None:
        if stop.is_set():
            return
        folder = pk.build(session, root)
        summary = run_session(folder, PROMPT, max_turns, transcripts / f"{session['session']}.jsonl")
        answers, parse_error = None, None
        path = folder / "answers.json"
        if path.exists():
            try:
                answers = json.loads(path.read_text(encoding="utf-8"))["answers"]
            except Exception as exc:  # noqa: BLE001
                parse_error = repr(exc)[:300]
        limit_hit = "limit" in (summary["result_text"] + summary["stderr"]).lower() and answers is None
        if limit_hit:
            stop.set()
            note_pause(set_name, session["session"], summary["result_text"] or summary["stderr"])
            return
        record = {"session": session["session"], "report_id": session["report_id"], "set": set_name,
                  "items": [i["item"] for i in session["items"]], "answers": answers, "parse_error": parse_error, **summary}
        with _lock:
            with open(out_path, "a", encoding="utf-8") as fh:
                fh.write(json.dumps(record) + "\n")
        print(f"{session['session']}: turns {summary['num_turns']}, {summary['seconds']}s, "
              f"{'ok' if answers is not None else 'NO ANSWERS'} ({summary['subtype']})", flush=True)

    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        list(ex.map(one, sessions))
    if stop.is_set():
        raise SystemExit("PAUSED: a usage limit stopped the run; rerun the same command later to resume")


def note_pause(set_name: str, session: str, message: str) -> None:
    path = RUNS / "spend.json"
    with _lock:
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
        state.setdefault("claude_pauses", []).append({"set": set_name, "session": session, "at": time.strftime("%Y-%m-%d %H:%M"), "message": message[:300]})
        path.write_text(json.dumps(state, indent=2), encoding="utf-8")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("command", choices=["isolation", "run"])
    ap.add_argument("--set", choices=["dev", "test", "queue"])
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--workers", type=int, default=3)
    ap.add_argument("--max-turns", type=int, default=MAX_TURNS)
    ap.add_argument("--tag", default="", help="suffix for development iterations, e.g. _v2")
    args = ap.parse_args()
    if args.command == "isolation":
        raise SystemExit(0 if isolation() else 1)
    run_set(args.set, args.only, args.workers, args.max_turns, args.tag)


if __name__ == "__main__":
    main()

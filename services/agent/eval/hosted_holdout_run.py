"""Run model-path holdout questions end to end and score them against the holdout labels.

Holdout rows carry Jev labels (disposition, tool, dataset), not runner expectations,
so a case passes when:
- status matches the labeled disposition (answer, clarification, unsupported),
- for answers, the labeled tool ran and succeeded and evidence is present,
- no executed filter is invented and no slot filter the question names is missing.

A "wrong answer" is status answer where the label is clarify or refuse, where the
labeled tool or dataset did not run, or where an invented filter executed.

    AGENT_LLM_PROVIDER=openrouter AGENT_ALLOW_REMOTE_PROVIDER=true \\
      python -m services.agent.eval.hosted_holdout_run --holdout v1=path.json --cap-usd 0.8
"""

from __future__ import annotations

import argparse
import asyncio
import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from services.agent.artifacts import ArtifactStore
from services.agent.config import AgentSettings
from services.agent.grounding import score_executed_filters
from services.agent.orchestrator import AgentOrchestrator
from services.agent.provider import OpenAICompatibleProvider
from services.agent.routing import route_question
from services.agent.tools import ToolExecutor

RUNS = Path(__file__).resolve().parent / "runs"
STATUS_FOR = {"answer": "answer", "clarify": "clarification", "unsupported": "unsupported"}
# visualization_* dataset names mapped to warehouse names used in labels.
DATASET_ALIASES = {"ignitions": "cpuc_ignitions", "epss": "epss_outages", "psps": "psps_events", "calfire": "calfire_incidents"}


class CostCapExceeded(RuntimeError):
    pass


class MeteredProvider(OpenAICompatibleProvider):
    """Adds up computed cost per request and refuses to start past the cap."""

    def __init__(self, settings: AgentSettings, cap_usd: float) -> None:
        super().__init__(settings)
        self.cap_usd = cap_usd
        self.spent = 0.0

    async def complete(self, **kwargs: Any):  # type: ignore[override]
        if self.spent >= self.cap_usd:
            raise CostCapExceeded(f"spend ${self.spent:.4f} reached cap ${self.cap_usd}")
        reply = await super().complete(**kwargs)
        self.spent += float(reply.usage.get("computed_cost_usd") or 0.0)
        return reply


def load_rows(path: Path) -> list[dict[str, Any]]:
    raw = json.loads(path.read_text(encoding="utf-8"))
    return raw.get("cases", raw) if isinstance(raw, dict) else raw


def score(row: dict[str, Any], response: dict[str, Any]) -> dict[str, Any]:
    trajectory = response.get("trajectory") or []
    calls = [e for e in trajectory if e.get("type") == "tool_call" and not e.get("qualification_call")]
    slots = route_question(row["question"]).slots
    filters = score_executed_filters(
        row["question"], calls, utilities=slots.get("utilities") or [], county=slots.get("county")
    )
    label = row.get("expected_disposition")
    status = response.get("status")
    status_pass = status == STATUS_FOR.get(str(label))
    ok_tools = [c.get("tool") for c in calls if c.get("ok")]
    ok_datasets = {
        DATASET_ALIASES.get(str(dataset), str(dataset))
        for c in calls
        if c.get("ok") and (dataset := (c.get("arguments") or {}).get("dataset")) is not None
    }
    expected_tool = row.get("expected_tool_pick")
    # A list label accepts any listed tool, as in the label rules.
    expected_tools = expected_tool if isinstance(expected_tool, list) else [expected_tool] if expected_tool else []
    expected_dataset = row.get("expected_dataset")
    tool_pass = True
    dataset_pass = True
    evidence_pass = True
    if label == "answer":
        tool_pass = not expected_tools or any(tool in ok_tools for tool in expected_tools)
        dataset_pass = not expected_dataset or not ok_datasets or expected_dataset in ok_datasets
        evidence = [e for e in response.get("evidence") or [] if not e.get("qualification_call")]
        evidence_pass = status != "answer" or (bool(evidence) and all(e.get("id") for e in evidence))
    wrong = status == "answer" and (
        label in {"clarify", "unsupported"} or not tool_pass or not dataset_pass or bool(filters["invented"])
    )
    return {
        "pass": status_pass and tool_pass and dataset_pass and evidence_pass and filters["pass"],
        "status_pass": status_pass,
        "tool_pass": tool_pass,
        "dataset_pass": dataset_pass,
        "evidence_pass": evidence_pass,
        "filters_pass": filters["pass"],
        "invented_filters": filters["invented"],
        "missing_filters": filters["missing"],
        "dropped_filters": [e for e in trajectory if e.get("type") == "filter_dropped"],
        "wrong_answer": wrong,
        "tools": [c.get("tool") for c in calls],
    }


async def run(args: argparse.Namespace) -> int:
    settings = AgentSettings.from_env()
    if not settings.hosted_llm:
        raise SystemExit("Set AGENT_LLM_PROVIDER=openrouter; this runner must not call the Ollama host.")
    provider = MeteredProvider(settings, args.cap_usd)
    records: list[dict[str, Any]] = []
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    out = RUNS / f"hosted_holdout_{stamp}.jsonl"
    try:
        with out.open("w", encoding="utf-8") as log:
            for spec in args.holdout:
                name, _, path = spec.partition("=")
                rows = [r for r in load_rows(Path(path)) if route_question(r["question"]).path == "model"]
                if args.ids:
                    wanted = {item.strip() for item in args.ids.split(",") if item.strip()}
                    rows = [r for r in rows if r.get("id") in wanted]
                print(f"[holdout] {name}: {len(rows)} model-path questions")
                for index, row in enumerate(rows, start=1):
                    executor = ToolExecutor(settings, ArtifactStore(settings.artifact_ttl_seconds))
                    orchestrator = AgentOrchestrator(settings, provider, executor)
                    before = provider.spent
                    started = time.perf_counter()
                    try:
                        result = await orchestrator.ask(row["question"])
                        response = result.response
                        error = None
                    except CostCapExceeded as exc:
                        print(f"[holdout] stopped: {exc}")
                        return 2
                    except Exception as exc:  # noqa: BLE001
                        response, error = {"status": "runner_error", "trajectory": []}, repr(exc)
                    finally:
                        await executor.close()
                    elapsed = time.perf_counter() - started
                    judged = score(row, response)
                    record = {
                        "holdout": name,
                        "id": row.get("id"),
                        "question": row["question"],
                        "label": row.get("expected_disposition"),
                        "expected_tool": row.get("expected_tool_pick"),
                        "expected_dataset": row.get("expected_dataset"),
                        "status": response.get("status"),
                        "answer_text": response.get("answer_text"),
                        "evidence": response.get("evidence"),
                        "trajectory": response.get("trajectory"),
                        "qualifications": response.get("qualifications"),
                        "elapsed_s": round(elapsed, 2),
                        "cost_usd": round(provider.spent - before, 6),
                        "runner_error": error,
                        **judged,
                    }
                    records.append(record)
                    log.write(json.dumps(record, default=str) + "\n")
                    log.flush()
                    print(
                        f"[holdout] {name} {index}/{len(rows)} {row.get('id')} "
                        f"{'PASS' if judged['pass'] else 'FAIL'} {elapsed:.1f}s "
                        f"spent ${provider.spent:.4f}",
                        flush=True,
                    )
    finally:
        await provider.close()
    print(f"[holdout] wrote {out} total ${provider.spent:.4f}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--holdout", action="append", required=True, help="name=path, repeatable")
    parser.add_argument("--cap-usd", type=float, default=0.8)
    parser.add_argument("--ids", default="", help="Comma-separated holdout ids to run.")
    return asyncio.run(run(parser.parse_args()))


if __name__ == "__main__":
    raise SystemExit(main())

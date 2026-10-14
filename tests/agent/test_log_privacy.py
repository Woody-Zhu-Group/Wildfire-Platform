"""Persistent diagnostics omit request text unless raw capture is explicit."""

import asyncio
import json
import os
import time
from dataclasses import replace
from unittest.mock import MagicMock

from services.agent.config import AgentSettings
from services.agent.decisions.shadow_log import ShadowLog
from services.agent.orchestrator import AgentOrchestrator
from services.agent.artifacts import ArtifactStore
from services.agent.tools import ToolExecutor
from tests.agent.test_jev_decide import FakeBackend
from tests.agent.test_v4_router import answers


def test_metadata_log_drops_raw_text_in_every_nested_payload(tmp_path):
    secret = "private location and injected diagnostic text"
    log = ShadowLog(str(tmp_path / "jev.jsonl"), 10000)
    log.write({"event": "jev_decide", "request_id": "request-1", "question": secret,
               "error": secret, "request_payload": {"state": {"question": secret}},
               "calls": [{"raw": secret}], "slots": {"query": secret},
               "jev": {"rule": "answer", "confidence": 0.9, "raw": secret}})
    text = log.path.read_text()
    assert secret not in text
    record = json.loads(text)
    assert record["request_id"] == "request-1"
    assert record["question_hash"]
    assert record["error"] is True
    assert record["jev"] == {"rule": "answer", "confidence": 0.9}


def test_raw_capture_is_explicit_and_still_redacts_keys(monkeypatch, tmp_path):
    monkeypatch.setenv("TYPESAFE_API_KEY", "test-private-key")
    log = ShadowLog(str(tmp_path / "jev.jsonl"), 10000, raw=True)
    log.write({"question": "synthetic query", "raw": "test-private-key"})
    record = log.read_records()[0]
    assert record["question"] == "synthetic query"
    assert record["raw"] == "[redacted]"


def test_retention_expires_only_this_logs_active_and_rotated_files(tmp_path):
    path = tmp_path / "jev.jsonl"
    for name in ("jev.jsonl", "jev.jsonl.1", "unrelated.jsonl", "jev.jsonl.notes"):
        item = tmp_path / name
        item.write_text('{"question":"old"}\n')
        os.utime(item, (time.time() - 8 * 86400,) * 2)
    log = ShadowLog(str(path), 10000, retention_days=7)
    assert not path.exists() and not (tmp_path / "jev.jsonl.1").exists()
    assert (tmp_path / "unrelated.jsonl").exists()
    assert (tmp_path / "jev.jsonl.notes").exists()
    log.write({"event": "new"})
    assert [record["event"] for record in log.read_records()] == ["new"]


def test_v4_stdout_and_default_disk_log_omit_question_and_slots(tmp_path, capsys):
    question = "What fires are near me?"
    agent = AgentOrchestrator(
        replace(AgentSettings(), jev_mode="v4", jev_log_path=str(tmp_path / "jev.jsonl")),
        MagicMock(), MagicMock(), decide_backend=FakeBackend(answers()),
    )
    asyncio.run(agent.ask(question))
    stdout = capsys.readouterr().out
    disk = (tmp_path / "jev.jsonl").read_text()
    assert question not in stdout + disk
    records = [json.loads(line) for line in stdout.splitlines()]
    assert all("slots" not in record and "question" not in record for record in records)


def test_invalid_tool_arguments_do_not_leak_to_stdout(capsys):
    private = "private user location in an invalid tool argument"

    async def run():
        executor = ToolExecutor(AgentSettings(), ArtifactStore(60))
        try:
            return await executor.execute(
                "data_query_records", {"dataset": private}, request_id="privacy-test", attempt=1
            )
        finally:
            await executor.close()

    result = asyncio.run(run())
    assert not result.ok
    assert private not in capsys.readouterr().out


def test_recent_appends_do_not_extend_the_oldest_records_retention(tmp_path):
    path = tmp_path / "jev.jsonl"
    from datetime import datetime, timedelta, timezone

    old = (datetime.now(timezone.utc) - timedelta(days=8)).isoformat()
    path.write_text(json.dumps({"logged_at": old, "question": "old"}) + "\n"
                    + json.dumps({"logged_at": datetime.now(timezone.utc).isoformat()}) + "\n")
    log = ShadowLog(str(path), 10000)
    assert not path.exists()
    log.write({"event": "new"})
    assert len(log.read_records()) == 1

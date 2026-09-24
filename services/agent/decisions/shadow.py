"""Background Jev runner. User requests never wait on it."""

from __future__ import annotations

import logging
import random
import threading
import weakref
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeout
from concurrent.futures.thread import _threads_queues, _worker
from datetime import datetime, timezone
from typing import Any

from services.agent.config import AgentSettings
from services.agent.decisions.backend import DecisionBackend, DecisionResult, QuestionSpec
from services.agent.decisions.call_budget import DailyCallBudget
from services.agent.decisions.integrity import answer_to_json, question_hash
from services.agent.decisions.mapping import agreement, model_tool_labels, regex_labels
from services.agent.decisions.schemas import (
    SCHEMA_VERSION,
    routing_questions,
    state_for,
    tool_pick_questions,
)
from services.agent.decisions.shadow_log import ShadowLog
from services.agent.routing import RouteDecision

logger = logging.getLogger("services.agent.decisions")

_RUNNER: "ShadowRunner | None" = None
_RUNNER_LOCK = threading.Lock()

# Bounds on the admitted-request map. An entry normally leaves when the outcome
# is written; a request that errors before that, or a streaming client that
# disconnects, would otherwise stay forever. Entries older than the TTL are
# evicted on the next submit, and the map never holds more than the size cap.
ADMITTED_TTL_SECONDS = 900.0
ADMITTED_MAX_SIZE = 10_000


class _DaemonExecutor(ThreadPoolExecutor):
    """Worker threads must not keep the process alive after shutdown.

    The base class starts the thread before we can mark it daemon, and Python
    refuses to change that flag once the thread is active.
    """

    def _adjust_thread_count(self) -> None:
        if self._idle_semaphore.acquire(timeout=0):
            return

        def weakref_cb(_ref, queue=self._work_queue):
            queue.put(None)

        if len(self._threads) < self._max_workers:
            thread = threading.Thread(
                name="%s_%d" % (self._thread_name_prefix or self, len(self._threads)),
                target=_worker,
                args=(
                    weakref.ref(self, weakref_cb),
                    self._work_queue,
                    self._initializer,
                    self._initargs,
                ),
                daemon=True,
            )
            thread.start()
            self._threads.add(thread)
            _threads_queues[thread] = self._work_queue


class ShadowRunner:
    def __init__(
        self,
        settings: AgentSettings,
        backend: DecisionBackend,
        *,
        log: ShadowLog | None = None,
        clock: Any = None,
        budget: DailyCallBudget | None = None,
    ) -> None:
        self.settings = settings
        self.backend = backend
        self.log = log or ShadowLog(
            settings.jev_log_path,
            max_bytes=int(settings.jev_log_max_mb * 1024 * 1024),
        )
        self._clock = clock or (lambda: datetime.now(timezone.utc))
        workers = max(1, int(settings.jev_max_concurrency))
        self._slots = threading.Semaphore(workers)
        self._executor = _DaemonExecutor(
            max_workers=workers, thread_name_prefix="jev-shadow"
        )
        self._lock = threading.Lock()
        self.dropped = 0
        # AGENT_JEV_DAILY_CALL_CAP counts API calls; see call_budget.py.
        self.budget = budget or DailyCallBudget(
            settings.jev_daily_call_cap, clock=self._clock
        )
        self._rng = random.Random()
        # request_id -> (admitted, admitted_at), insertion ordered for eviction.
        self._admitted: OrderedDict[str, tuple[bool, datetime]] = OrderedDict()

    @property
    def cap_blocked(self) -> int:
        return self.budget.blocked

    @property
    def calls_today(self) -> int:
        return self.budget.calls_today

    def admitted_size(self) -> int:
        with self._lock:
            return len(self._admitted)

    def _evict_admitted(self, now: datetime) -> None:
        """Caller holds self._lock. Drop stale entries, then the oldest past the size cap."""
        while self._admitted:
            oldest_id, (_flag, seen) = next(iter(self._admitted.items()))
            if (now - seen).total_seconds() > ADMITTED_TTL_SECONDS:
                self._admitted.popitem(last=False)
                continue
            break
        while len(self._admitted) > ADMITTED_MAX_SIZE:
            self._admitted.popitem(last=False)

    def shutdown(self, timeout: float = 2.0) -> None:
        self._executor.shutdown(wait=False, cancel_futures=True)
        deadline = self._clock()
        # Abandon anything still running after the grace period. Daemon threads
        # will not block process exit; this just stops new work.
        del deadline, timeout

    def submit_routing(
        self,
        request_id: str,
        question: str,
        decision: RouteDecision,
        today: str,
        *,
        forced: bool,
        candidate_tools: list[str] | None = None,
    ) -> None:
        admitted = self._sampled()
        now = self._clock()
        with self._lock:
            self._admitted[request_id] = (admitted, now)
            self._evict_admitted(now)
        if not admitted:
            return
        if not self._reserve(self._calls_per_question(question, today, candidate_tools)):
            return
        state = state_for(question, today)
        questions = routing_questions()
        digest = question_hash(question)
        regex = regex_labels(decision)
        self._executor.submit(
            self._run_routing,
            request_id,
            question,
            digest,
            forced,
            decision.path,
            decision.rule,
            regex,
            state,
            questions,
            list(candidate_tools or []),
        )

    @property
    def bundles_tool_pick(self) -> bool:
        """v3 sends tool pick inside the question record, so it is not a second cap hit."""
        return getattr(self.settings, "jev_ablation", "v3_split") != "v2_full"

    def was_admitted(self, request_id: str) -> bool:
        with self._lock:
            entry = self._admitted.get(request_id)
            return bool(entry and entry[0])

    def _calls_per_question(
        self, question: str, today: str, candidate_tools: list[str] | None
    ) -> int:
        """How many API calls submit_routing will make for this question."""
        if not self.bundles_tool_pick:
            return 1
        from services.agent.decisions.v3 import calls_for_config

        config = getattr(self.settings, "jev_ablation", "v3_split")
        return max(1, len(calls_for_config(question, today, candidate_tools or None, config)))

    def submit_tool_pick(
        self,
        request_id: str,
        question: str,
        candidate_tools: list[str],
        *,
        forced: bool,
    ) -> None:
        if not self.was_admitted(request_id) or not candidate_tools:
            return
        if not self._reserve(1):
            return
        today = self._clock().date().isoformat()
        state = state_for(question, today)
        questions = tool_pick_questions(list(candidate_tools))
        digest = question_hash(question)
        self._executor.submit(
            self._run_tool_pick,
            request_id,
            question,
            digest,
            forced,
            list(candidate_tools),
            state,
            questions,
        )

    def record_outcome(
        self,
        request_id: str,
        question: str,
        response: dict[str, Any] | None,
    ) -> None:
        """Local write only. Queued so ask() does not wait on disk or on Jev."""
        self._executor.submit(self._write_outcome, request_id, question, response)

    def _sampled(self) -> bool:
        rate = float(self.settings.jev_sample_rate)
        if rate >= 1:
            return True
        if rate <= 0:
            return False
        return self._rng.random() < rate

    def _reserve(self, calls: int) -> bool:
        """Take a concurrency slot and `calls` of today's API call budget, or neither."""
        if not self._slots.acquire(blocking=False):
            with self._lock:
                self.dropped += 1
            self._write_now(
                {
                    "type": "dropped",
                    "schema_version": SCHEMA_VERSION,
                    "ts": self._clock().isoformat(),
                    "reason": "concurrency",
                }
            )
            return False
        if not self.budget.reserve(calls):
            self._slots.release()
            self._write_now(
                {
                    "type": "dropped",
                    "schema_version": SCHEMA_VERSION,
                    "ts": self._clock().isoformat(),
                    "reason": "daily_cap",
                    "calls": calls,
                }
            )
            return False
        return True

    def _run_routing(
        self,
        request_id: str,
        question: str,
        digest: str,
        forced: bool,
        path: str,
        rule: str,
        regex: dict[str, Any],
        state: dict[str, str],
        questions: dict[str, QuestionSpec],
        candidate_tools: list[str] | None = None,
    ) -> None:
        if self.bundles_tool_pick:
            try:
                self._run_v3(
                    request_id,
                    question,
                    digest,
                    forced,
                    path,
                    rule,
                    regex,
                    candidate_tools or [],
                )
            finally:
                self._slots.release()
            return
        try:
            result = self._evaluate(request_id, digest, state, questions)
            if result is None:
                record = self._empty_routing(
                    request_id, question, digest, forced, path, rule, regex, state, questions,
                    error="backend returned None",
                )
                self._write_now(record)
                return
            if not self._identity_ok(result, request_id, digest, question):
                return
            jev_answers = {
                name: answer_to_json(answer) for name, answer in result.answers.items()
            }
            agree = (
                agreement(regex, result.answers) if result.answers and not result.error else None
            )
            self._write_now(
                {
                    "type": "routing",
                    "schema_version": SCHEMA_VERSION,
                    "request_id": request_id,
                    "ts": self._clock().isoformat(),
                    "question": question,
                    "question_hash": digest,
                    "forced": forced,
                    "backend": self.backend.name,
                    "model_version": result.model_version,
                    "regex": {"path": path, "rule": rule, "labels": regex},
                    "unmapped_rule": bool(regex.get("unmapped_rule")),
                    "request_payload": _payload(state, questions, self.settings.jev_model),
                    "jev": None
                    if result.error or not result.answers
                    else {
                        "answers": jev_answers,
                        "raw": result.raw,
                        "latency_ms": result.latency_ms,
                        "input_tokens": result.input_tokens,
                    },
                    "agree": agree,
                    "unexpected_option": result.unexpected_option,
                    "error": result.error,
                }
            )
        finally:
            self._slots.release()

    def _run_tool_pick(
        self,
        request_id: str,
        question: str,
        digest: str,
        forced: bool,
        candidate_tools: list[str],
        state: dict[str, str],
        questions: dict[str, QuestionSpec],
    ) -> None:
        try:
            result = self._evaluate(request_id, digest, state, questions)
            if result is None:
                self._write_now(
                    {
                        "type": "tool_pick",
                        "schema_version": SCHEMA_VERSION,
                        "request_id": request_id,
                        "ts": self._clock().isoformat(),
                        "question": question,
                        "question_hash": digest,
                        "forced": forced,
                        "candidate_tools": candidate_tools,
                        "request_payload": _payload(state, questions, self.settings.jev_model),
                        "jev": None,
                        "latency_ms": None,
                        "unexpected_option": False,
                        "error": "backend returned None",
                    }
                )
                return
            if not self._identity_ok(result, request_id, digest, question):
                return
            self._write_now(
                {
                    "type": "tool_pick",
                    "schema_version": SCHEMA_VERSION,
                    "request_id": request_id,
                    "ts": self._clock().isoformat(),
                    "question": question,
                    "question_hash": digest,
                    "forced": forced,
                    "candidate_tools": candidate_tools,
                    "backend": self.backend.name,
                    "model_version": result.model_version,
                    "request_payload": _payload(state, questions, self.settings.jev_model),
                    "jev": None
                    if result.error or not result.answers
                    else {
                        "answers": {
                            name: answer_to_json(answer)
                            for name, answer in result.answers.items()
                        },
                        "raw": result.raw,
                        "latency_ms": result.latency_ms,
                        "input_tokens": result.input_tokens,
                    },
                    "latency_ms": result.latency_ms,
                    "unexpected_option": result.unexpected_option,
                    "error": result.error,
                }
            )
        finally:
            self._slots.release()

    def _run_v3(
        self,
        request_id: str,
        question: str,
        digest: str,
        forced: bool,
        path: str,
        rule: str,
        regex: dict[str, Any],
        candidate_tools: list[str],
    ) -> None:
        from concurrent.futures import ThreadPoolExecutor

        from services.agent.decisions.integrity import parse_raw_answers
        from services.agent.decisions.jev_policy import derive_outcome, facts_from_answers
        from services.agent.decisions.v3 import SCHEMA_VERSION as V3_VERSION
        from services.agent.decisions.v3 import calls_for_config

        config = getattr(self.settings, "jev_ablation", "v3_split")
        calls = calls_for_config(
            question,
            self._clock().date().isoformat(),
            candidate_tools or None,
            config,
        )

        def run_call(call: dict[str, Any]) -> tuple[dict[str, Any], DecisionResult | None]:
            result = self._evaluate(request_id, digest, call["state"], call["questions"])
            return call, result

        with ThreadPoolExecutor(max_workers=max(1, len(calls))) as pool:
            finished = list(pool.map(run_call, calls))

        call_records = []
        merged: dict[str, Any] = {}
        errors: list[str] = []
        unexpected = False
        model_version = None
        for call, result in finished:
            payload = _payload(call["state"], call["questions"], self.settings.jev_model)
            if result is None:
                errors.append("backend returned None")
                call_records.append({"name": call["name"], "request_payload": payload, "error": "backend returned None"})
                continue
            if not self._identity_ok(result, request_id, digest, question):
                return
            if result.raw:
                try:
                    _parsed, flag = parse_raw_answers(result.raw, call["questions"])
                    unexpected = unexpected or flag
                except ValueError as exc:
                    errors.append(str(exc))
            if result.error:
                errors.append(result.error)
            else:
                merged.update({name: answer_to_json(answer) for name, answer in result.answers.items()})
            model_version = model_version or result.model_version
            call_records.append(
                {
                    "name": call["name"],
                    "request_payload": payload,
                    "raw": result.raw,
                    "latency_ms": result.latency_ms,
                    "input_tokens": result.input_tokens,
                    "unexpected_option": result.unexpected_option,
                    "error": result.error,
                }
            )
            unexpected = unexpected or bool(result.unexpected_option)
        facts = facts_from_answers(merged)
        outcome = derive_outcome(facts, question=question)
        self._write_now(
            {
                "type": "routing",
                "schema_version": V3_VERSION,
                "ablation_config": config,
                "request_id": request_id,
                "ts": self._clock().isoformat(),
                "question": question,
                "question_hash": digest,
                "forced": forced,
                "backend": self.backend.name,
                "model_version": model_version,
                "regex": {"path": path, "rule": rule, "labels": regex},
                "unmapped_rule": bool(regex.get("unmapped_rule")),
                "request_payload": call_records[0]["request_payload"] if call_records else {},
                "calls": call_records,
                "facts": facts.__dict__,
                "outcome": {
                    "disposition": outcome.disposition,
                    "clarify_reason": outcome.clarify_reason,
                    "unsupported_topic": outcome.unsupported_topic,
                    "trace": outcome.trace,
                    "confidence": outcome.confidence,
                },
                "jev": None
                if errors or not merged
                else {"answers": merged},
                "agree": None,
                "unexpected_option": unexpected,
                "error": "; ".join(errors) if errors else None,
            }
        )

    def _evaluate(
        self,
        request_id: str,
        digest: str,
        state: dict[str, str],
        questions: dict[str, QuestionSpec],
    ) -> DecisionResult | None:
        inner = _DaemonExecutor(max_workers=1, thread_name_prefix="jev-call")
        future = inner.submit(
            self.backend.evaluate,
            state,
            questions,
            request_id=request_id,
            question_hash=digest,
        )
        try:
            return future.result(timeout=float(self.settings.jev_timeout_seconds))
        except FuturesTimeout:
            logger.warning(
                "Jev call timed out after %ss", self.settings.jev_timeout_seconds
            )
            return DecisionResult(
                answers={},
                model_version=None,
                latency_ms=float(self.settings.jev_timeout_seconds) * 1000,
                input_tokens=None,
                raw={},
                request_id=request_id,
                question_hash=digest,
                error=(
                    "TimeoutError: Jev call exceeded "
                    f"{self.settings.jev_timeout_seconds}s"
                ),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Jev backend raised %s: %s", type(exc).__name__, exc)
            return DecisionResult(
                answers={},
                model_version=None,
                latency_ms=0,
                input_tokens=None,
                raw={},
                request_id=request_id,
                question_hash=digest,
                error=f"{type(exc).__name__}: {exc}",
            )
        finally:
            inner.shutdown(wait=False, cancel_futures=True)

    def _identity_ok(
        self,
        result: DecisionResult,
        request_id: str,
        digest: str,
        question: str,
    ) -> bool:
        if result.request_id == request_id and result.question_hash == digest:
            return True
        logger.warning(
            "Jev wiring error: response identity %s/%s did not match job %s/%s",
            result.request_id,
            result.question_hash,
            request_id,
            digest,
        )
        self._write_now(
            {
                "type": "wiring_error",
                "schema_version": SCHEMA_VERSION,
                "request_id": request_id,
                "ts": self._clock().isoformat(),
                "question": question,
                "question_hash": digest,
                "claimed_request_id": result.request_id,
                "claimed_question_hash": result.question_hash,
                "error": "response identity did not match the job it was sent for",
            }
        )
        return False

    def _write_outcome(
        self,
        request_id: str,
        question: str,
        response: dict[str, Any] | None,
    ) -> None:
        status = None if response is None else response.get("status")
        route = {} if response is None else (response.get("route") or {})
        trajectory = [] if response is None else (response.get("trajectory") or [])
        labels = model_tool_labels(trajectory, str(status or ""))
        timings = {} if response is None else (response.get("timings_ms") or {})
        with self._lock:
            self._admitted.pop(request_id, None)
        self._write_now(
            {
                "type": "outcome",
                "schema_version": SCHEMA_VERSION,
                "request_id": request_id,
                "ts": self._clock().isoformat(),
                "question": question,
                "question_hash": question_hash(question),
                "status": status,
                "route_path": route.get("path"),
                "model_first_tools": labels["model_first_tools"],
                "model_final_tools": labels["model_final_tools"],
                "answer_origin": route.get("answer_origin"),
                "elapsed_ms": timings.get("total"),
            }
        )

    def _empty_routing(
        self,
        request_id: str,
        question: str,
        digest: str,
        forced: bool,
        path: str,
        rule: str,
        regex: dict[str, Any],
        state: dict[str, str],
        questions: dict[str, QuestionSpec],
        *,
        error: str,
    ) -> dict[str, Any]:
        return {
            "type": "routing",
            "schema_version": SCHEMA_VERSION,
            "request_id": request_id,
            "ts": self._clock().isoformat(),
            "question": question,
            "question_hash": digest,
            "forced": forced,
            "backend": self.backend.name,
            "model_version": None,
            "regex": {"path": path, "rule": rule, "labels": regex},
            "unmapped_rule": bool(regex.get("unmapped_rule")),
            "request_payload": _payload(state, questions, self.settings.jev_model),
            "jev": None,
            "agree": None,
            "unexpected_option": False,
            "error": error,
        }

    def _write_now(self, record: dict[str, Any]) -> None:
        try:
            self.log.write(record)
        except Exception as exc:  # noqa: BLE001
            logger.warning("Jev shadow log write failed: %s: %s", type(exc).__name__, exc)


def _payload(
    state: dict[str, str],
    questions: dict[str, QuestionSpec],
    model: str,
) -> dict[str, Any]:
    return {
        "state": state,
        "model": model,
        "questions": {name: spec.payload() for name, spec in questions.items()},
    }


def get_runner(settings: AgentSettings) -> ShadowRunner:
    """Process-wide runner. Constructed only when mode is shadow."""
    global _RUNNER
    with _RUNNER_LOCK:
        if _RUNNER is None:
            from services.agent.decisions.typesafe_backend import make_backend

            backend = make_backend(
                settings.jev_backend,
                model=settings.jev_model,
                timeout_seconds=settings.jev_timeout_seconds,
            )
            _RUNNER = ShadowRunner(settings, backend)
        return _RUNNER


def reset_runner_for_tests() -> None:
    global _RUNNER
    with _RUNNER_LOCK:
        if _RUNNER is not None:
            _RUNNER.shutdown()
        _RUNNER = None

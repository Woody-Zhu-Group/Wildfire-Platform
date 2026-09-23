"""Environment-backed configuration for the agent prototype."""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from urllib.parse import urlparse

from dotenv import load_dotenv

from shared.db import REPO_ROOT

load_dotenv(REPO_ROOT / ".env")


def _bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class AgentSettings:
    provider: str = "openai_compatible"
    model_base_url: str = "http://127.0.0.1:11434/v1"
    model_api_key: str = "ollama"
    model: str = "qwen2.5:7b"
    model_runtime: str | None = None
    thinking: str = "off"
    # Constrained synthesis returns tokens reliably; prompt mode often times out.
    structured_mode: str = "constrained"
    # Routing stays thinking-off; synthesis may enable thinking separately.
    # Default false: on CPU-only hosts, synthesis with think=true exceeded the
    # 180s timeout in probes; set AGENT_SYNTHESIS_THINKING=true on GPU/hosted.
    synthesis_thinking: bool = False
    request_timeout_seconds: float = 900.0
    max_completion_tokens: int = 1800
    max_routing_tokens: int = 900
    max_synthesis_tokens: int = 1200
    max_tool_steps: int = 5
    max_validation_retries: int = 2
    # Ollama defaults to 4096 when unset; configure more only when needed.
    num_ctx: int = 32768
    synthesis_timeout_seconds: float = 180.0
    seed: int = 42
    temperature: float = 0.0
    allow_remote_provider: bool = False
    # Architecture experiment only. Default remains deterministic-first.
    disable_deterministic_routing: bool = False
    artifact_ttl_seconds: int = 900
    data_query_url: str = "http://127.0.0.1:8000"
    risk_url: str = "http://127.0.0.1:8001"
    visualization_url: str = "http://127.0.0.1:8002"
    comparison_url: str = "http://127.0.0.1:8003"
    # Jev shadow mode. Default off never imports typesafe_sdk.
    jev_mode: str = "off"
    jev_backend: str = "typesafe"
    jev_model: str = "jev-latest"
    jev_timeout_seconds: float = 3.0
    jev_sample_rate: float = 1.0
    jev_max_concurrency: int = 4
    jev_daily_call_cap: int = 5000
    jev_log_path: str = "services/agent/logs/jev_shadow.jsonl"
    jev_log_max_mb: float = 50.0
    jev_ablation: str = "v3_hybrid"

    @classmethod
    def from_env(cls) -> "AgentSettings":
        value = cls(
            provider=os.getenv("AGENT_PROVIDER", "openai_compatible"),
            model_base_url=os.getenv(
                "AGENT_MODEL_BASE_URL", "http://127.0.0.1:11434/v1"
            ).rstrip("/"),
            model_api_key=os.getenv("AGENT_MODEL_API_KEY", "ollama"),
            model=os.getenv("AGENT_MODEL", "qwen2.5:7b"),
            model_runtime=os.getenv("AGENT_MODEL_RUNTIME") or None,
            thinking=os.getenv("AGENT_THINKING", "off").strip().lower(),
            structured_mode=os.getenv(
                "AGENT_STRUCTURED_MODE", "constrained"
            ).strip().lower(),
            synthesis_thinking=_bool("AGENT_SYNTHESIS_THINKING", False),
            request_timeout_seconds=float(
                os.getenv("AGENT_TIMEOUT_SECONDS", "900")
            ),
            max_completion_tokens=int(
                os.getenv("AGENT_MAX_COMPLETION_TOKENS", "1800")
            ),
            max_routing_tokens=int(os.getenv("AGENT_MAX_ROUTING_TOKENS", "900")),
            max_synthesis_tokens=int(
                os.getenv("AGENT_MAX_SYNTHESIS_TOKENS", "1200")
            ),
            max_tool_steps=int(os.getenv("AGENT_MAX_TOOL_STEPS", "5")),
            max_validation_retries=int(
                os.getenv("AGENT_MAX_VALIDATION_RETRIES", "2")
            ),
            num_ctx=int(os.getenv("AGENT_NUM_CTX", "32768")),
            synthesis_timeout_seconds=float(
                os.getenv("AGENT_SYNTHESIS_TIMEOUT_SECONDS", "180")
            ),
            seed=int(os.getenv("AGENT_SEED", "42")),
            temperature=float(os.getenv("AGENT_TEMPERATURE", "0")),
            allow_remote_provider=_bool("AGENT_ALLOW_REMOTE_PROVIDER", False),
            disable_deterministic_routing=_bool(
                "AGENT_DISABLE_DETERMINISTIC_ROUTING", False
            ),
            artifact_ttl_seconds=int(os.getenv("AGENT_ARTIFACT_TTL_SECONDS", "900")),
            data_query_url=os.getenv(
                "DATA_QUERY_BASE_URL", "http://127.0.0.1:8000"
            ).rstrip("/"),
            risk_url=os.getenv(
                "RISK_FORECASTING_BASE_URL", "http://127.0.0.1:8001"
            ).rstrip("/"),
            visualization_url=os.getenv(
                "VISUALIZATION_BASE_URL", "http://127.0.0.1:8002"
            ).rstrip("/"),
            comparison_url=os.getenv(
                "COMPARISON_BASE_URL", "http://127.0.0.1:8003"
            ).rstrip("/"),
            jev_mode=os.getenv("AGENT_JEV_MODE", "off").strip().lower(),
            jev_backend=os.getenv("AGENT_JEV_BACKEND", "typesafe").strip().lower(),
            jev_model=os.getenv("AGENT_JEV_MODEL", "jev-latest").strip(),
            jev_timeout_seconds=float(os.getenv("AGENT_JEV_TIMEOUT_SECONDS", "3")),
            jev_sample_rate=float(os.getenv("AGENT_JEV_SAMPLE_RATE", "1")),
            jev_max_concurrency=int(os.getenv("AGENT_JEV_MAX_CONCURRENCY", "4")),
            jev_daily_call_cap=int(os.getenv("AGENT_JEV_DAILY_CALL_CAP", "5000")),
            jev_log_path=os.getenv(
                "AGENT_JEV_LOG_PATH", "services/agent/logs/jev_shadow.jsonl"
            ),
            jev_log_max_mb=float(os.getenv("AGENT_JEV_LOG_MAX_MB", "50")),
            jev_ablation=os.getenv("AGENT_JEV_ABLATION", "v3_hybrid").strip(),
        )
        value.validate()
        return value

    def validate(self) -> None:
        if self.provider != "openai_compatible":
            raise ValueError("AGENT_PROVIDER must be openai_compatible")
        if self.thinking not in {"off", "on"}:
            raise ValueError("AGENT_THINKING must be off|on")
        if self.structured_mode not in {"prompt", "constrained"}:
            raise ValueError("AGENT_STRUCTURED_MODE must be prompt|constrained")
        if self.num_ctx < 2048:
            raise ValueError("AGENT_NUM_CTX must be >= 2048")
        if self.synthesis_timeout_seconds < 5:
            raise ValueError("AGENT_SYNTHESIS_TIMEOUT_SECONDS must be >= 5")
        if self.jev_mode in {"verify", "fallback", "route"}:
            raise ValueError(
                f"AGENT_JEV_MODE={self.jev_mode} is reserved and not implemented. "
                "Use off or shadow."
            )
        if self.jev_mode not in {"off", "shadow"}:
            raise ValueError("AGENT_JEV_MODE must be off or shadow")
        if self.jev_backend != "typesafe":
            raise ValueError("AGENT_JEV_BACKEND must be typesafe")
        if self.jev_timeout_seconds <= 0:
            raise ValueError("AGENT_JEV_TIMEOUT_SECONDS must be positive")
        if not 0 <= self.jev_sample_rate <= 1:
            raise ValueError("AGENT_JEV_SAMPLE_RATE must be between 0 and 1")
        if self.jev_max_concurrency < 1:
            raise ValueError("AGENT_JEV_MAX_CONCURRENCY must be >= 1")
        if self.jev_daily_call_cap < 0:
            raise ValueError("AGENT_JEV_DAILY_CALL_CAP must be >= 0")
        if self.jev_ablation not in {
            "v2_full",
            "v3_split",
            "v3_single",
            "v3_no_glossary",
            "v3_policy_context",
            "v3_hybrid",
        }:
            raise ValueError(
                "AGENT_JEV_ABLATION must be v2_full, v3_split, v3_single, "
                "v3_no_glossary, v3_policy_context, or v3_hybrid"
            )
        if not self.allow_remote_provider and not _is_loopback(self.model_base_url):
            raise ValueError(
                "Remote model providers are blocked. Security review and "
                "AGENT_ALLOW_REMOTE_PROVIDER=true are required."
            )
        for name, url in {
            "DATA_QUERY_BASE_URL": self.data_query_url,
            "RISK_FORECASTING_BASE_URL": self.risk_url,
            "VISUALIZATION_BASE_URL": self.visualization_url,
            "COMPARISON_BASE_URL": self.comparison_url,
        }.items():
            if not _is_loopback(url):
                raise ValueError(f"{name} must remain a loopback URL")

    def with_eval_cell(
        self, *, model: str, thinking: str, structured_mode: str
    ) -> "AgentSettings":
        updated = replace(
            self,
            model=model,
            model_runtime=None,
            thinking=thinking,
            structured_mode=structured_mode,
        )
        updated.validate()
        return updated

    @property
    def request_model(self) -> str:
        return self.model_runtime or self.model

    def with_runtime_model(self, runtime_model: str) -> "AgentSettings":
        return replace(self, model_runtime=runtime_model)


def _is_loopback(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in {"127.0.0.1", "localhost", "::1"}

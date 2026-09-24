"""Who made the answer, clarify, or refuse decision for one question.

decision_source() reads only the final route and the jev_decide summary that
decide mode already stores in the slots. It never carries Jev's raw payload:
the Jev fields are the disposition and one confidence number.

source:
  backstop  a router hard backstop fired (decide_mode.BACKSTOP_RULES); rule is its id
  jev       decide mode applied Jev's disposition; disposition and confidence
  router    the router's decision stands; why says why Jev did not decide
mode: the AGENT_JEV_MODE in force (off, shadow, tool_pick, tool_pick_template, decide).
"""

from __future__ import annotations

from typing import Any

from services.agent.decisions.decide_mode import BACKSTOP_RULES, ROUTER_DISPOSITION

# decide_mode.DecideResult.why -> the router "why" shown to users.
_DECIDE_WHY: dict[str, str] = {
    "below_gate": "jev_below_gate",
    "error": "jev_error",
    "timeout": "jev_timeout",
    "daily_cap": "jev_daily_cap",
    "code_verified": "verified_fact",
    "contradicts_slot": "verified_fact",
    "regex_only": "router_only_route",
    "router_only_tool": "router_only_route",
    "agree": "jev_agreed",
}

# Modes where Jev does not make the answer, clarify, or refuse decision.
_MODE_WHY: dict[str, str] = {
    "off": "jev_off",
    "shadow": "jev_shadow",
    "tool_pick": "jev_tool_pick",
    "tool_pick_template": "jev_tool_pick",
}

ROUTER_WHYS = frozenset(_DECIDE_WHY.values()) | frozenset(_MODE_WHY.values()) | {
    "jev_skipped"
}


def _confidence(value: Any) -> float | None:
    return round(float(value), 4) if isinstance(value, (int, float)) else None


def decision_source(
    *, path: str, rule: str, slots: dict[str, Any], jev_mode: str
) -> dict[str, Any]:
    """The decision source for the final route of one question."""
    mode = jev_mode or "off"
    decide = slots.get("jev_decide") if isinstance(slots.get("jev_decide"), dict) else None
    router_rule = (decide or {}).get("router_rule") or rule
    if router_rule in BACKSTOP_RULES and (decide is None or decide.get("winner") != "jev"):
        return {"source": "backstop", "rule": router_rule, "mode": mode}
    if decide is not None:
        if decide.get("winner") == "jev":
            return {
                "source": "jev",
                "disposition": ROUTER_DISPOSITION.get(path, "answer"),
                "confidence": _confidence(decide.get("jev_confidence")),
                "mode": mode,
            }
        why = _DECIDE_WHY.get(str(decide.get("why")), "jev_error")
        out: dict[str, Any] = {"source": "router", "why": why, "mode": mode}
        if why in {"jev_below_gate", "jev_agreed"}:
            out["jev_disposition"] = decide.get("jev_disposition")
            out["jev_confidence"] = _confidence(decide.get("jev_confidence"))
        return out
    if mode == "decide":
        # Decide mode skipped this request (forced-model evaluation).
        return {"source": "router", "why": "jev_skipped", "mode": mode}
    return {"source": "router", "why": _MODE_WHY.get(mode, "jev_off"), "mode": mode}

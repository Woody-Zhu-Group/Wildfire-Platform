"""Every Jev call sends the payload measured for PR 43.

The hashes below were computed on platform/main (c624c58) with today pinned to
2026-09-22, model jev-latest, config v3_hybrid. They are stored, not regenerated,
so a change to a question, an option, a glossary, or the context fails here
before it reaches shadow, tool_pick, tool_pick_template, or a decide mode.
Re-pinning them is a deliberate context change and needs the accuracy and
confidence report CLAUDE.md asks for.
"""

import pytest

from services.agent.decisions.canonical import payload_hash
from services.agent.decisions.shadow import _payload
from services.agent.decisions.v3 import calls_for_config, tool_pick_call
from services.agent.routing import candidate_tools

TODAY = "2026-09-22"
MODEL = "jev-latest"

PINNED = {
    "How many EPSS outages occurred in 2024?": {
        "tools": ["data_query_records"],
        "facts": "e40291568e5a5a8c44f6b67bc21ba42fb69059b3f7041a61b04b521c3a9b4b26",
        "topic": "eeccb59855fbbc4e59cd6e33e213a176967bd39bf6d0a40acab44f7aeff456e5",
        "places": "a34506a7c3cc8e39033da30bf9c4f26c1b2c5e14c3350aa263d7c9f21dcfdad2",
        "tool_pick": "585a88b7defaa6f6db692ddec4f24eba3a9f014c4d2f3d01b4fe58f3e409cd80",
    },
    "Show a weekly CPUC ignition time series for 2024.": {
        "tools": ["visualization_create"],
        "facts": "1131358d94be0d006962c0371a991e19a2bee17bd2a9dc36ca392b89d2f59ea3",
        "topic": "3c15d8d43dcbc6c0a5966109d80678991d1e2247be5e61a28a1c49e0bdf3870f",
        "places": "8571aa16ff7e88eb20894e9be18c10a4d5c5a94eb4eaa8e5f86fe24b532874b1",
        "tool_pick": "21333250cb91625012bf6e65ea95d2f3468f848f8fb9ac8e3860cc9777caa821",
    },
    "How many US ignition sample events occurred in 2024?": {
        "tools": ["data_query_records"],
        "facts": "ead63738eb7a4d48c0d262684d8d988fac698808ffb08c70a6e93f916f72268b",
        "topic": "8db93aa5455d36d217d67440ba830a0d8b2b0b2449849fcb27006965ab443e48",
        "places": "03b3c6081e01530d6a884f89063b8a9e70bf99aa0f38fc09618ca87a1d62c927",
        "tool_pick": "2404fda7ac60616ef7e85dbdf7a97ab15ee1c6b0ef4a8eb105608161d8092cf5",
    },
}
FACT_NOULS = {
    "has_time_scope", "vague_time", "future_time", "names_specific_place", "vague_proximity",
    "broad_region", "asks_risk", "names_risk_metric", "prompt_injection",
}


def _hash(call):
    return payload_hash(_payload(call["state"], call["questions"], MODEL))


@pytest.mark.parametrize("question", sorted(PINNED))
def test_every_v3_call_hashes_to_the_stored_pr43_payload(question):
    pinned = PINNED[question]
    assert candidate_tools(question) == pinned["tools"], "candidate tools changed; re-pin deliberately"
    calls = {call["name"]: call for call in calls_for_config(question, TODAY, pinned["tools"], "v3_hybrid")}
    for name in ("facts", "topic", "places", "tool_pick"):
        assert _hash(calls[name]) == pinned[name], name


@pytest.mark.parametrize("question", sorted(PINNED))
def test_the_live_tool_pick_call_hashes_to_the_stored_payload(question):
    pinned = PINNED[question]
    live = tool_pick_call(question, TODAY, pinned["tools"], "v3_hybrid")
    assert _hash(live) == pinned["tool_pick"]


def test_the_facts_call_is_exactly_the_nine_nouls():
    calls = {call["name"]: call for call in calls_for_config("How many EPSS outages occurred in 2024?", TODAY, None, "v3_hybrid")}
    assert set(calls["facts"]["questions"]) == FACT_NOULS

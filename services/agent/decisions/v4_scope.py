"""Experimental v4 fact replacement; original v3/v4 payloads stay reproducible."""

from services.agent.decisions import v4
from services.agent.decisions.backend import QuestionSpec

SCHEMA_VERSION = "v4_scope_v1"
SCOPE_FACT = "missing_geographic_scope"
SCOPE_INSTRUCTIONS = (
    "The request still lacks geographic scope information required for its requested operation. "
    "Judge missing or unresolved location or boundary information, not the size of the area. "
    "A named statewide area, county, utility territory, grid cell, or coordinates is defined. "
    "A count or list without any requested geographic restriction uses the dataset's full coverage. "
    "A statewide grid or model-performance query does not need a narrower location. "
    "An inventory lookup by identifier needs no geographic filter. "
    "A needed point or local location, an unnamed county, or an undefined boundary needs clarification."
)


def calls_for(question: str, today: str) -> list[dict]:
    calls = v4.calls_for(question, today)
    facts = next(call for call in calls if call["name"] == "facts")
    facts["state"]["schema_version"] = SCHEMA_VERSION
    facts["questions"] = {
        SCOPE_FACT if name == "broad_region" else name: QuestionSpec(
            kind="noul", instructions=SCOPE_INSTRUCTIONS
        )
        if name == "broad_region"
        else spec
        for name, spec in facts["questions"].items()
    }
    return calls

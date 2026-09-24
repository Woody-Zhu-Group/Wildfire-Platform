# Agent security posture

This prototype is intentionally low-risk under lethal-trifecta and Rule-of-Two
reasoning because its capabilities and trust boundary are narrow:

- Read-only tools only.
- One trusted local user.
- No third-party or untrusted content ingestion.
- No database writes, filesystem-write tool, or external messaging. The only
  outbound channels are the opt-in remote model paths listed below; all are off
  by default.
- Loopback-only model URL by default. Backend service URLs must always be
  loopback; there is no override for them.
- One exchange at a time; no conversation memory.

Prompt injection is not treated as reliably solvable by filtering. Safety comes
from architecture: tool schemas expose only bounded reads, backend response
content is treated as data, full payloads stay out of model context, unsupported
claims are blocked without tool evidence, and startup rejects a non-loopback
model URL, or any Jev mode other than `off`, unless `AGENT_ALLOW_REMOTE_PROVIDER=true`
is set.

## Opt-in remote paths on main (all off by default)

Each of these sends the user's question text off the host:

- `AGENT_LLM_PROVIDER=openrouter` sends routing and synthesis prompts, including
  tool summaries, to OpenRouter. Startup requires `AGENT_ALLOW_REMOTE_PROVIDER=true`
  and `OPENROUTER_API_KEY`. See [`docs/OPENROUTER.md`](../../docs/OPENROUTER.md).
- `AGENT_JEV_MODE=shadow`, `tool_pick`, `tool_pick_template`, or `decide` sends
  the question to Jev at TypeSafe (`AGENT_JEV_BACKEND=typesafe`, key
  `TYPESAFE_API_KEY`) or at OpenRouter (`AGENT_JEV_BACKEND=openrouter`, key
  `OPENROUTER_API_KEY`). Every one of these modes requires
  `AGENT_ALLOW_REMOTE_PROVIDER=true` and the active backend's key; startup fails
  with a clear message otherwise, so no Jev mode can be turned on without the same
  explicit opt-in as the LLM provider. Spend is bounded per process per UTC day by
  `AGENT_JEV_DAILY_CALL_CAP`, counted in API calls. See
  [`docs/JEV_SHADOW.md`](../../docs/JEV_SHADOW.md) and
  [`docs/JEV_DECIDE.md`](../../docs/JEV_DECIDE.md).

Keys are read from the environment or `.env` and are kept out of logs: the
settings object hides the model key from its repr, startup errors never print a
key, and the shadow log redacts both the TypeSafe and the OpenRouter key.

The API sets CORS `allow_origins=["*"]` so the local website can call it; bind
uvicorn to loopback (its default) so that only this machine can reach it.

## Changes that require threat-model review

Do not add any of the following casually:

- web search or arbitrary URL retrieval
- document/file upload or third-party content ingestion
- database, filesystem, ticketing, email, or messaging writes
- public or multi-user access
- a remote model provider or any new external communication channel
- persistent conversation memory

Any one of these changes the trust boundary. Combining untrusted input, private
data, and an external side effect can create the lethal trifecta. A security
review must precede implementation, with authentication, authorization, tenant
isolation, data-retention, audit, egress, and prompt-injection consequences
documented explicitly.

## Non-goals

This prototype is not hardened for public deployment, hostile users, uploaded
documents, or secrets in prompts. It should run on loopback in a trusted local
environment only.

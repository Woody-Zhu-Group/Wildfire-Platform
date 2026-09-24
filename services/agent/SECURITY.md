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
model URL unless `AGENT_ALLOW_REMOTE_PROVIDER=true` is set.

## Opt-in remote paths on main (all off by default)

Each of these sends the user's question text off the host:

- `AGENT_LLM_PROVIDER=openrouter` sends routing and synthesis prompts, including
  tool summaries, to OpenRouter. Startup requires `AGENT_ALLOW_REMOTE_PROVIDER=true`
  and `OPENROUTER_API_KEY`. See [`docs/OPENROUTER.md`](../../docs/OPENROUTER.md).
- `AGENT_JEV_MODE=shadow`, `tool_pick`, or `tool_pick_template` sends the question
  to Jev at TypeSafe (`AGENT_JEV_BACKEND=typesafe`, key `TYPESAFE_API_KEY`) or at
  OpenRouter (`AGENT_JEV_BACKEND=openrouter`, key `OPENROUTER_API_KEY`). These modes
  are **not** gated by `AGENT_ALLOW_REMOTE_PROVIDER`; turning one on is itself the
  opt-in. See [`docs/JEV_SHADOW.md`](../../docs/JEV_SHADOW.md).

Keys are read from the environment or `.env` and are kept out of logs: the
settings object hides the model key from its repr, startup errors never print a
key, and the shadow log redacts the TypeSafe key.

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

# Jev determinism

Checked on 2026-09-22 against TypeSafe SDK 0.7.1 and the public Python API reference.

## What we looked for

The installed SDK (`typesafe_sdk`) and the client docs at
https://docs.typesafe.ai/sdk/python/api/clients/sync.md list these `TypeSafeClient`
parameters: `api_key`, `model`, `retry`, `timeout`, `headers`, `transport`,
`http_client`, `base_url`. `system_one` takes `state`, `questions`, `model`,
`retry`, `timeout`, `extra_headers`, `extra_body`, and `response_model`.

`extra_body` is a generic shallow merge into the JSON body. The docs do not
name a seed, temperature, deterministic flag, or sampling mode. A search of
the installed package for `seed`, `temperature`, `determinist`, and `sampling`
only hit `functools.cached_property`. Request bytes are produced by
`typesafe_sdk._core.json.serialize`, which calls `pydantic_core.to_json`
without sorting keys.

No `AGENT_JEV_SEED` setting was added, because the API does not document a
place to send one.

## How we check that two calls are the same request

Replay hashes the stored payload as canonical JSON (sorted keys, compact
separators) and hashes the raw request body captured from the HTTP transport
in eval mode. A canonical-hash mismatch is a wiring bug. An answer change
with identical canonical hashes is variance, not a failed replay.

## Repeat test

Model `jev-1.13.0`. Source log `jev_offline_20260922T045006Z.jsonl`.
20 stored payloads, 10 sends each. Example stored canonical hash:
`463ba759df9c15a5e5d30b42ec42b26a969a4c26083667f5f93a67f30017f96a`.

Every resend produced one unique raw body (`sent_unique=1`). The raw body
hash differs from the stored canonical hash because the SDK's `to_json` does
not sort keys. After parsing the body and hashing it the same way as the
stored payload, mismatches were 0. The requests are identical. Answer changes
are model variance.

Choice labels flipped only in the 0.5–0.6 confidence bucket (1 of 6 questions,
flip rate 0.17). From 0.6 upward, including every answer at 0.8 or above
(n=87), the flip rate was 0. The four questions that changed a label were
low-confidence ones: `fp_close_to_500` disposition 0.44 and clarify_reason
0.46, `ambiguous_near_me` disposition 0.30 and intent 0.51,
`collision_trend_calfire_sce_territory` disposition 0.31, and
`ratio_per_circuit` unsupported_topic 0.42.

Noul yes/no decisions (probability crossing 0.5) did not flip in any distance
bucket (n=171). An earlier 12-payload pass counted any float change as a
Noul flip and is not the result to use.

```
choice confidence | n | flip rate
0.5-0.6 | 6 | 0.17
0.6-0.7 | 2 | 0.00
0.7-0.8 | 7 | 0.00
0.8-0.9 | 11 | 0.00
0.9-1.0 | 76 | 0.00
noul distance from 0.5 | n | flip rate
0.0-0.1 | 1 | 0.00
0.1-0.2 | 4 | 0.00
0.2-0.3 | 9 | 0.00
0.3-0.4 | 35 | 0.00
0.4-0.5 | 122 | 0.00
```

## Draft note for TypeSafe support

We send the same `system_one` body to `jev-1.13.0` ten times. The HTTP body
is byte-identical across those sends. Choice labels at confidence 0.7 and
above stayed put. Labels between 0.5 and 0.6 sometimes changed, and choice
probabilities still move by roughly 0.01 to 0.17 even when the label does
not. Is `jev-1.13.0` intended to be deterministic for an identical body? SDK
0.7.1 has no seed, temperature, or deterministic argument on `TypeSafeClient`
or `system_one`. If one exists, which field should we set?

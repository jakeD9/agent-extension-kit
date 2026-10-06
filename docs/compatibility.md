# Genkit and OpenAI compatibility gate

S07 selects the framework and provider seam before the production coordinator is built. The default
gate is deterministic and credential-free. It does not claim that a live OpenAI request passed.

## Pinned matrix

| Component | Version | Verified behavior |
|---|---:|---|
| Python | 3.12 (`>=3.12,<3.13` in the adapter) | local gate |
| Genkit Python | 0.12.0 | experimental agents, flows, tools, streaming, sessions |
| `genkit-openai` | 0.12.0 | package/import compatibility only |
| `genkit-middleware` | 0.12.0 | authorized `Skills` projection |
| `genkit-fastapi` | 0.12.0 | agent and flow routes; flow HTTP request |
| FastAPI | 0.142.2 | TestClient request |
| HTTPX | 0.28.1 | clients and deterministic transports |
| Pydantic | 2.13.5 | tool inputs and structured outputs |
| OpenAI Python | 3.24.0 | custom Responses adapter |
| Genkit CLI | 1.43.0 | pinned trace command surface |

Direct Python dependencies are exact pins in `team-agent-runtime-genkit` and `uv.lock`. The CLI is
pinned in root compatibility metadata and invoked as `npx --yes genkit-cli@1.43.0`; it is developer
tooling rather than an application dependency. An initial Genkit 0.11.0 / middleware 0.12.0 mix
failed at import because middleware used the newer artifact API. The all-0.12.0 matrix fixes that
release skew.

## Selected paths

The coordinator path is `genkit.exp.Genkit.define_agent`. Genkit 0.12.0's stable `genkit.Genkit`
does not expose `define_agent`; the agent API is explicitly experimental. Genkit types remain inside
`team_agent_runtime_genkit`. If an upgrade breaks this gate, retain the provider-neutral runtime
contract and use Genkit flows plus application-owned history until agent parity is restored.

The provider path is the small `openai_responses` Genkit model adapter with explicit model ID
`gpt-6-astra`. The pinned stock `genkit-openai` plugin uses Chat Completions. It remains installed for
compatibility tracking but is not the selected coordinator adapter because OpenAI requires the
Responses API for GPT-6 Astra tool calling. Official references:

- [GPT-6 Astra model](https://developers.openai.com/api/docs/models/gpt-6-astra)
- [Function calling](https://developers.openai.com/api/docs/guides/function-calling)
- [Responses streaming](https://developers.openai.com/api/docs/guides/streaming-responses)

The custom seam maps ordered text/function-call/function-result parts, function definitions, call
IDs, strict JSON-schema output, streamed text, usage, terminal response states, safe provider
failures, and cooperative abort signals to the Responses API. It supports Genkit `tool_choice` plus
the explicitly allowlisted `max_output_tokens` and `reasoning_effort` config fields. Unknown config
fields fail rather than disappearing silently.

Responses assistant phases (`commentary` and `final_answer`) are stored in Genkit `Part.metadata`
under `openai_responses_phase` and restored on replay; a serialized two-phase round trip is covered.
Because requests use `store=false`, the adapter requests `reasoning.encrypted_content` and stores the
validated Responses reasoning item in the adjacent Genkit reasoning part metadata. Serialized
history and agent tool-loop replay restore that item in its original position before the associated
function call or assistant message. Only the pinned SDK's reasoning item shape is accepted; forged
or future item types fail closed rather than reaching the provider.
Provider refusals become a `blocked` finish with a safe message. Unknown response content fails
closed, and abort cleanup gives the stream one owner so provider `close()` runs exactly once.

Structured-output schemas must already be closed and fully required; the adapter refuses to rewrite
an open object or typed dictionary because replacing `additionalProperties` would change its
meaning. Tool schemas are preserved byte-for-structure: compatible closed schemas use strict mode,
while open or optional schemas are sent with `strict=false`. Arbitrary caller reasoning parts, plus
media, data, resource, and custom message parts, are not translated and fail with
`INVALID_ARGUMENT`. The adapter deliberately does not expose OpenAI hosted tools as Genkit
capabilities in S07.

## Deterministic evidence

The credential-free gate proves:

- a Pydantic tool call and multi-step tool loop through a mocked Responses client, including
  encrypted reasoning replay before the function call and result;
- Pydantic structured output through an already-strict Responses JSON schema, including nested
  `$defs`, plus non-mutating typed-dictionary rejection/preservation tests;
- a real agent `send_stream` tool loop, streamed Responses events, and a final Genkit response;
- a second agent turn resumed from a stored snapshot;
- custom `get_snapshot`, atomic `save_snapshot`, and status subscription hooks;
- `Skills` middleware reading an installer-produced generic projection only;
- detached work transitioning from `pending` to `aborted`, including cancellation and closure of a
  stalled provider stream, plus cancellation of a stalled non-stream request;
- completed/incomplete/failed response-state handling, token-usage mapping, safe stream errors, and
  exactly-once stream closure;
- refusal/unknown-content handling, serialized `commentary`/`final_answer` phase replay, and
  serialized reasoning-item replay before a later final message;
- mounted `serve_agent` and `serve_flow` routes plus a real typed flow HTTP request.

Run it with:

```bash
cd /Users/duckworth/projects/agent-extension-kit
UV_CACHE_DIR=/private/tmp/agent-extension-kit-uv-cache \
  uv run pytest packages/runtime_genkit_py/tests/test_compatibility.py
```

The `team-agent-genkit-compat` executable also runs the local gate. `--skill-root` must point to a
generic projection previously created by `team-agent skills pull --target generic`; it does not
silently use the complete extension tree.

## Opt-in live OpenAI check

There is no implicit live model fallback. Configure the exact model ID and credentials explicitly:

```bash
cd /Users/duckworth/projects/agent-extension-kit
export OPENAI_API_KEY='...'
export TEAM_AGENT_OPENAI_MODEL='gpt-6-astra'
export RUN_LIVE_GENKIT_OPENAI=1
UV_CACHE_DIR=/private/tmp/agent-extension-kit-uv-cache \
  uv run team-agent-genkit-compat --live-openai
```

This makes one short Responses request with `store=false`. It was not run during the deterministic
gate, so model availability, account access, billing, and live behavior are not claimed.

## Trace inspection

Run a probe under the pinned CLI to create traces; direct `uv run` execution does not populate the
CLI trace store:

```bash
npx --yes genkit-cli@1.43.0 start --non-interactive -- \
  uv run team-agent-genkit-compat --skill-root /absolute/path/to/generic/projection
npx --yes genkit-cli@1.43.0 trace:list
npx --yes genkit-cli@1.43.0 trace:get TRACE_ID --format json
```

`trace:list` is human-oriented in CLI 1.43.0 and has no format flag; use it to select an ID, then use
`trace:get --format json` for machine-readable inspection. `start` is a foreground process. The
deterministic suite verifies pinned command construction, but does not claim a stored live-model
trace. The CLI help check passed; npm reported deprecations in transitive CLI dependencies, which do
not enter the Python runtime image.

## Deferred boundaries

S07 adds no production coordinator, MongoDB session store, durable job, Slack behavior, provider
fallback, or coding-harness invocation. The recording store proves only that the pinned Genkit API
can persist and resume snapshots; it is not the production conversation design. S09 owns the
coordinator, and S10 owns application-level durable conversations with no Genkit `SessionStore`.

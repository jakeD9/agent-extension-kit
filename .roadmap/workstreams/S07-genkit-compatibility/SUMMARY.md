# S07 Summary — Genkit Python and OpenAI Compatibility Gate

## Delivered

- Pinned Python 3.12.2, Genkit/OpenAI/middleware/FastAPI packages at the mutually compatible
  0.12.0 line, OpenAI Python 3.24.0, and Genkit CLI 1.43.0.
- Selected `genkit.exp.Genkit.define_agent`; the stable 0.12.0 `genkit.Genkit` does not expose the
  experimental agent API. The documented fallback remains Genkit flows with application-owned
  history if a future release breaks the agent compatibility gate.
- Added `team-agent-runtime-genkit` with an executable deterministic compatibility probe covering
  Pydantic tools, strict structured output, streaming, stored continuation, custom session-store
  hooks, authorized Skills middleware projection, abort, FastAPI agent/flow routes, and trace
  command discovery.
- Added a narrow `gpt-6-astra` Genkit model adapter over the OpenAI Responses API because the pinned
  stock `genkit-openai` plugin uses Chat Completions and cannot satisfy Codex-first Astra tool use.
- The adapter fails closed on unsupported roles, parts, schemas, config, response items, and states;
  maps explicit request controls, tools, status, finish reasons, refusals, token usage, and safe
  provider failures; and supports cancellation for stalled stream and non-stream requests.
- With `store=false`, encrypted reasoning items and Codex commentary/final-answer phases survive
  Genkit message serialization and replay in provider order across tool loops and later turns.

## Verification evidence

- The focused compatibility suite passed 21 tests. The full deterministic suite passed 177 tests;
  three opt-in live MongoDB/harness tests were skipped.
- Ruff lint/format, root strict mypy plus direct strict test typing, lock consistency, CLI help,
  `git diff --check`, and builds for all eight Python distributions passed after the final changes.
- Deterministic tests use the real Genkit agent/tool/session/middleware/FastAPI surfaces with fake
  provider responses. They prove reasoning-before-call-before-output replay, structured schemas,
  streamed tool loops, status/usage mapping, and cancellation/cleanup behavior.
- Genkit CLI 1.43.0 was inspected directly: `trace:list` is human-oriented, while `trace:get`
  supports `--format json`. Transitive CLI npm warnings do not enter the Python runtime image.
- No live OpenAI request or trace ran because credentials were not configured. Model availability,
  account access, billing, and live Astra behavior remain explicitly unverified and opt-in.

## S08/S09 inputs and boundaries

- Genkit types remain inside `team_agent_runtime_genkit`; provider-neutral context, skill, job, and
  persistence contracts must not import them.
- The stock `genkit-openai` package is pinned for compatibility tracking but is not the selected
  Astra coordinator adapter. There is no model/provider fallback or silent model substitution.
- The Skills middleware accepts only an installer-produced authorized generic projection, never the
  full extension tree.
- S07 adds no production coordinator, MongoDB session store, durable coding job, Slack behavior, or
  coding-harness invocation. S08 owns governed memory, S09 the coordinator, and S10 durable chats.

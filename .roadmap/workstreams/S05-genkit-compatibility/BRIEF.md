# S05 — Genkit Python and OpenAI Compatibility Gate

Depends on S04. Read `.agents/skills/developing-genkit-python/SKILL.md` and its routed references.
Pin Python, Genkit, `genkit-openai`, Genkit CLI, middleware, FastAPI integration, and related versions.
Build a small executable that uses an explicitly configured Codex-capable OpenAI API model ID and
proves agent creation or the flows fallback, Pydantic tool calls, structured output, streaming,
continuation, custom session-store hooks, Skills middleware, abort behavior, FastAPI integration,
and trace inspection. Record exact results and the selected agent-or-flow path in
`docs/compatibility.md`. Default CI is deterministic; any live OpenAI check is opt-in and must not be
claimed without credentials and fresh evidence. Exclude the production coordinator and Mongo store.

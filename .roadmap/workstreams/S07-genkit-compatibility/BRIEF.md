# S07 — Genkit Python and OpenAI Compatibility Gate

> Historical compatibility brief. Its Genkit/provider evidence remains valid and does not establish
> content authority or access policy.

Depends on S06. Read `.agents/skills/developing-genkit-python/SKILL.md` and its routed references.
Pin Python, Genkit, `genkit-openai`, Genkit CLI, middleware, FastAPI integration, and related versions.
Build a small executable that uses an explicitly configured Codex-capable OpenAI API model ID and
proves agent creation or the flows fallback, Pydantic tool calls, structured output, streaming,
continuation, custom session-store hooks, Skills middleware against an installed generic skill
projection, abort behavior, FastAPI integration, and trace inspection. Record exact results and the selected agent-or-flow path in
`docs/compatibility.md`. Default CI is deterministic; any live OpenAI check is opt-in and must not be
claimed without credentials and fresh evidence. Exclude the production coordinator and Mongo store.

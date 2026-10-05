# S09 — Genkit Coordinator Tracer

Depends on S06, S07, and the completed S08 domain-authority correction. Do not start against the
superseded per-artifact access or memory-approval contracts. Define provider-neutral
`AgentTurnRequest`, `AgentTurnResult`, and
`AgentRuntime`; keep all Genkit types inside `runtime_genkit`. Implement the pinned OpenAI model
factory, scoped REST context tools, runtime CLI, and deterministic CI adapter. Use the shared
`SkillClient` and resolved immutable manifest to install only the selected generic projection for
`genkit_middleware.Skills`; never expose paths outside the admitted domain extension. Record the selected lock/revision
on the run. Execute one skill-guided cited turn with explicit status, decisions, usage, and failures.
Exclude durable conversations and coding jobs. Verify tool invocation and skill use through outputs
plus Genkit traces; live OpenAI checks remain opt-in. Verify canonical Git citations are distinct
from supplemental working memory and win any conflict.

# S07 — Genkit Coordinator Tracer

Depends on S04 and S05. Define provider-neutral `AgentTurnRequest`, `AgentTurnResult`, and
`AgentRuntime`; keep all Genkit types inside `runtime_genkit`. Implement the pinned OpenAI model
factory, scoped REST context tools, runtime CLI, and deterministic CI adapter. Use `SkillLoader` to
resolve authorization and pin revisions, then project only selected skills into
`genkit_middleware.Skills`; never expose the full extension tree. Execute one skill-guided cited turn
with explicit status, decisions, usage, and failures. Exclude durable conversations and coding jobs.
Verify tool invocation and skill use through outputs plus Genkit traces; live OpenAI checks remain
opt-in.

# S09 — Genkit Coordinator Tracer

Depends on S06, S07, and the completed S08 correction. Prove that the hosted Genkit coordinator can
execute one useful grounded turn using the same Git knowledge, selected skills, and supplemental
memory available to local agents. This is not the local developer interface: local Codex and Claude
sessions continue to use installed project skills and `team-context` MCP or context CLI. S09 exposes
only `uv run team-agent-runtime invoke < request.json` as a narrow diagnostic/CI probe.

Define provider-neutral `AgentTurnRequest`, `AgentTurnResult`, and `AgentRuntime`. The bounded
request contains run ID, project, objective, and selected skill names. The result contains status,
text, observed citations, explicit application-level decisions, exact content revision and skill
lock, usage, and safe failure details. Keep Genkit types inside `runtime_genkit`.

For each invocation, validate the request; resolve selected project skills; create an immutable
lock; install only those packages into a temporary generic projection; construct request-scoped
`genkit_middleware.Skills`; register project-bound `search_team_knowledge` and
`search_team_memory` tools; execute one Genkit turn through the explicit OpenAI/Codex-capable model
and existing Responses adapter; derive citations from actual tool outputs; then delete the temporary
projection. Trusted code binds project, authentication, context URL, and environment. Do not expose
in-turn skill discovery, memory mutation, jobs, or Git operations. Canonical Git evidence and
supplemental memory remain distinct, and Git wins conflicts with the decision made observable.

Capture usage at the Responses adapter boundary; deterministic fixtures must label synthetic usage.
The probe reads one JSON request from stdin, writes one JSON result to stdout, and uses bounded
structured errors on stderr with a nonzero exit. Configuration supplies context URL/token,
`deterministic` or `openai` mode, model ID, and API key.

Verify real Genkit middleware and tool machinery with a deterministic adapter: selected-skill use,
observed tool calls and citations, Git/memory distinction and precedence, exact skill/content
revisions, adapter usage, cancellation, cleanup, safe failures, probe parity, and a Genkit trace.
Live OpenAI remains opt-in. Exclude durable conversations, Mongo runtime state, interactive chat,
Slack, coding jobs, background/resume behavior, mutation, Git writes, multi-agent work, additional
providers, and any competing local coding-agent UX.

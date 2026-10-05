# Stateless Genkit coordinator

S09 implements one provider-neutral `AgentRuntime.execute()` turn with Genkit isolated behind
`team_agent_runtime_genkit`. The request intentionally contains only a run ID, logical project,
objective, and selected skill names. Conversations, jobs, Slack identity, repository execution, and
durable runtime state remain outside this slice.

Before generation, the runtime resolves the selected skills through the authenticated `SkillClient`
and installs their exact immutable packages into a fresh generic projection. `Skills` receives only
that temporary projection. During the single `ai.generate()` call, the model can call only
`use_skill`, project-bound `search_team_knowledge`, and project-bound `search_team_memory`; the model
cannot choose a project or credential. The projection is removed after the turn.

Completed results distinguish canonical Git-backed knowledge citations from supplemental shared
memory citations. A turn completes only when every selected skill and both context tools completed,
and every canonical citation revision equals the recorded skill catalog/content revision. The
`canonical_knowledge_over_memory` decision makes the conflict rule observable. Usage is labeled
`observed` for OpenAI Responses or `synthetic` for the deterministic CI adapter.

## JSON CLI

The CLI reads one bounded request from stdin and writes one result to stdout:

```bash
export TEAM_AGENT_CONTEXT_URL=http://127.0.0.1:8080
export TEAM_AGENT_TOKEN=development-token
export TEAM_AGENT_MODEL_MODE=openai
export TEAM_AGENT_OPENAI_MODEL=gpt-6-astra
export OPENAI_API_KEY=your-api-key

printf '%s' '{"run_id":"run-1","project":"platform","objective":"Summarize the recovery procedure","skill_names":["incident-guide"]}' \
  | team-agent-runtime invoke
```

`TEAM_AGENT_MODEL_MODE=deterministic` selects the credential-free model adapter for CI, but still
uses the real authenticated context and skill services. It is never an OpenAI fallback. Invalid
configuration or input produces bounded JSON on stderr; an explicit failed runtime result is emitted
on stdout with a nonzero status.

## Trace verification

Run the same CLI under the pinned Genkit CLI to capture the generate and tool spans:

```bash
printf '%s' '{"run_id":"trace-1","project":"platform","objective":"Summarize recovery","skill_names":["incident-guide"]}' \
  | npx --yes genkit-cli@1.43.0 start --non-interactive -- team-agent-runtime invoke
npx --yes genkit-cli@1.43.0 trace:list
npx --yes genkit-cli@1.43.0 trace:get <trace_id> --format json
```

The trace must contain `use_skill`, `search_team_knowledge`, and `search_team_memory`. Result
decisions and citations are application-observed evidence from successful callbacks; model-authored
citations are not trusted.

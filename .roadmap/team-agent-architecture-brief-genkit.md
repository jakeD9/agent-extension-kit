# Team Agent Architecture: Genkit Python Runtime and Portable Extension Kit

Status: Implementation handoff — supersedes the earlier ADK brief
Updated: 2026-10-04
Audience: Codex and the engineering team

The implementation requirements in `skills-distribution-addendum.md` are normative where they
replace this brief's earlier skill-catalog or distribution design.

## 1. Goal and architectural decisions

Build a deployment-neutral, Docker-packaged team agent that supports Slack threads, a CLI, event and scheduled automations, and bounded multi-agent workflows. Use Genkit Python for hosted reasoning and coordination. Preserve a provider-neutral extension kit that local Codex and Claude Code can use independently of Genkit.

The extension kit supplies approved knowledge, reusable skills, persistent scoped memory, and narrow internal tools. Genkit operates hosted agents that consume those capabilities. Disposable coding runners invoke Codex or Claude Code for repository work. Application code owns authorization, durable execution, validation, and publication.

Decisions:

- Python control plane and context service; use uv, a locked dependency set, typing, Ruff, and pytest.
- Genkit Python is the hosted runtime. Keep its types inside a runtime adapter package. Python is preview and the Agents API is beta; verify the installed release before relying on newer agent features.
- Python owns services, orchestration, contracts, and runner supervision. A pinned Codex executable is an external dependency; no TypeScript application service is required.
- The initial coordinator target is a Codex-capable OpenAI model through Genkit's Python OpenAI
  plugin, using an explicit API model ID verified in the compatibility gate. Gemini and Anthropic
  remain later provider-contract targets. Model agnostic does not mean identical feature behavior.
- MongoDB cluster is the only required durable database. Use separate `agent_runtime` and `team_context` databases and credentials.
- REST is the primary context-service interface; MCP and CLI adapt the same application services.
- Git owns canonical knowledge, conventions, and skills. The context service builds skill discovery
  metadata in memory and distributes immutable packages through authenticated APIs. MongoDB owns
  knowledge indexes, runtime records, and dynamic memory, not the skill catalog.
- Codex is the initial coding harness. Claude Code is a later adapter behind the same contract.
- Produce three images from documented Dockerfile targets: `company/team-agent`, `company/team-context`, and `company/coding-runner`.
- Provide Dockerfiles and development Compose only. Do not add Kubernetes, ECS, Nomad, or production hosting manifests.
- Implement one coordinator first, then a bounded parallel review workflow. Do not introduce an unrestricted agent swarm.

## 2. Service and trust boundaries

| Component | Owns | Must not own |
|---|---|---|
| Team agent | Slack ingress, identities, conversations, Genkit execution, workflow routing, automation definitions and runs | Raw shared-context database access, repository shell execution |
| Team context service | Authorized retrieval, ingestion, skills catalog, shared memory, internal tools, audit | Slack thread state, arbitrary orchestration |
| Coding runner | Pinned checkout, harness invocation, tests, diff/result validation | Long-lived conversation authority, production access |
| Runner supervisor | Credentials, sandbox policy, independent checks, permitted branch/PR publication | Trusting the model's success assertion as verification |
| Job executor | Launch, status, cancel, cleanup | Agent reasoning |

```mermaid
flowchart TD
    Slack[Slack threads] --> Agent[Team agent: Genkit runtime]
    CLI[Team agent CLI] --> Agent
    Triggers[Events and scheduler] --> Agent
    Agent --> Context[Team context service]
    Agent --> Executor[Job executor]
    Executor --> Runner[Disposable Codex or Claude runner]
    Runner --> Context
    Local[Local developer agents] --> MCP[MCP adapter]
    MCP --> Context
    Context --> Git[Git knowledge and skills]
    Context --> ContextDB[MongoDB team_context]
    Agent --> RuntimeDB[MongoDB agent_runtime]
```

Agent delegation and execution isolation are independent. A Genkit agent or tool delegates to a coding adapter; the executor provisions its environment. Use clear names in code: `GenkitRuntime`, `CodingJobExecutor`, `CodingHarness`, and `CodingRunner`. A model plugin is not a Codex/Claude coding harness.

## 3. Repository layout

```text
team-agent/
  apps/
    team_agent/             # HTTP, scheduler, worker entry points
    context_service/        # REST and MCP transports
    coding_runner/          # supervisor and harness adapters
    cli/                    # runtime and context CLI commands
  packages/
    contracts/              # Pydantic contracts and JSON schemas
    auth/                   # identities, scopes, policy decisions
    runtime_genkit/         # agents, model adapters, Genkit session adapter
    workflows/              # bounded workflow definitions and reconciliation
    jobs/                   # executor contracts and local Docker implementation
    automations/            # matching, schedules, leases, cursors
    context_client/         # authenticated REST client
    database/               # MongoDB repositories, validators, indexes
    git_providers/          # mock first; GitHub/GitLab adapters later
    observability/
  extension/
    skills/
      diagnose-and-fix/SKILL.md
      review-merge-request/SKILL.md
      architecture-review/SKILL.md
    knowledge/
      architecture/
      domain/
      conventions/
      glossary/
    adapters/
      codex/
      claude/
  tests/
    fixtures/
    integration/
    provider_contracts/
  evals/
  docs/
  Dockerfile
  compose.yaml
  pyproject.toml
  uv.lock
  README.md
```

## 4. Genkit Python integration

Implement a narrow domain interface:

```python
from typing import Protocol

class AgentRuntime(Protocol):
    async def execute(self, request: AgentTurnRequest) -> AgentTurnResult: ...
```

Requests include run ID, authenticated principal reference, conversation ID, objective, project/repository scope, context references, pinned skill revisions, and capability policy. Results include text, citations, structured decisions, usage, pending external jobs, and explicit status. Genkit types belong only in `runtime_genkit`; use an opaque, versioned envelope when retaining framework state.

### Framework responsibilities and application responsibilities

| Capability | Genkit contribution | Application code |
|---|---|---|
| Reasoning and model calls | Model plugins, generation, tool loop | Provider configuration and capability tests |
| Conversational coordinator | Python agent API where verified | Slack identity, thread mapping, turn serialization |
| Specialist delegation | Agent calls or tools wrapping specialist flows | Allowed graph, budgets, reconciliation |
| Persistent conversation | Session-store interface | Custom MongoDB store, retention and fencing |
| External coding | Tool wrapping a coding-job adapter | Queue, container executor, harness adapter |
| Retrieval | Tools and optional retriever integration | Context service, scope filtering, MongoDB search |
| Automations | Invoked agent/flow | Event matcher, scheduler, leases and idempotency |
| Observability | Flow/model/tool traces and developer UI | Redaction, job/action audit, retention |

Use `ai.define_agent()` for the conversational coordinator if supported in the pinned release. Use `@ai.flow()` for typed specialist operations and explicit workflow stages, and `@ai.tool()` for narrowly scoped capabilities. These are documented Python APIs; verify their exact signatures and imports in the selected release. Do not translate TypeScript examples mechanically.

Genkit flows are ordinary callable workflow functions with tracing and schemas. Treat the application job store as the authority for cross-process orchestration and recovery. Native detached agent turns, if adopted later, remain separate from container jobs.

Expose these logical tools:

- `search_team_knowledge`, `get_team_document`
- `list_team_skills`, `get_team_skill`
- `search_team_memory`, `propose_team_memory`
- `get_incident`, `get_repository_metadata`
- `start_coding_job`, `get_coding_job`, `cancel_coding_job`

Bind caller identity and allowed scope in trusted application code. A model cannot grant itself permissions through arguments. Validate arguments and enforce authorization in the destination service as well.

A Genkit coding specialist can wrap `start_coding_job`, but actual editing and command execution use the Codex/Claude harness in the runner. Do not implement a second homemade repository tool loop in Genkit.

### Python compatibility gate

Before implementing the real runtime, pin a Python version, Genkit release, provider plugins, and harness versions in `uv.lock` and image build inputs. Document the tested matrix in `docs/compatibility.md`.

Verify agent creation, tool calls, structured output, streaming, sessions, custom store hooks, abort behavior, and FastAPI integration against executable examples. Check specialist delegation in Python explicitly: availability in TypeScript or Go is not evidence of Python parity.

If delegation middleware is unavailable, expose specialists as Genkit tools backed by flows. Use an explicit Python workflow state machine for fixed order, parallel branches, and joins. If the agent API fails required tests, implement the same `AgentRuntime` interface with Genkit flows/`generate()` and application-owned history. Keep Genkit and Python; record the fallback. Do not introduce a TypeScript service, LangGraph, or another orchestrator without a concrete need.

### Model providers

Use a configuration-driven model factory. Support OpenAI first through the pinned `genkit-openai`
plugin and an explicit Codex-capable API model ID. The compatibility gate selects and records the
exact tested ID rather than treating “Codex” as an API identifier. Keep Anthropic and Gemini
configurations extensible. Use provider plugins supported by the installed Python release; a thin
custom model adapter is permissible if documented and tested. Do not route through LiteLLM by
default.

The OpenAI model plugin and `CodexHarness` are different adapters. The former performs hosted
coordinator reasoning through Genkit. The latter invokes the pinned Codex executable inside a
disposable coding container. Neither may substitute for the other's trust boundary.

Opus and Sol are compatibility targets, not API identifiers or guarantees of feature parity. Configure exact API model IDs and endpoints. Do not silently discard reasoning parameters, change models, or fall back to a different provider.

Provider contract tests cover multi-step tools, structured output with tools, streamed arguments when used, conversation continuation, reasoning continuity where required, usage accounting, and failure reporting. CI uses synthetic fixtures; credential-dependent tests are opt-in and bounded.

### Skills and context

Canonical skills remain Git-owned and provider neutral. The context service scans validated packages
from one pinned checkout into an in-memory discovery catalog; MongoDB is not required for skill
discovery or download. Each package includes trigger metadata, prerequisites, procedure, constraints,
evidence requirements, completion criteria, and a complete resource inventory. V1 skill packages are
self-contained and do not declare skill-to-skill dependencies.

The shared Python `SkillClient` resolves caller authorization and named/all selections to a versioned
manifest pinned to one immutable catalog revision. The `SkillInstaller` validates safe paths and
hashes, writes a snake_case lock and ownership manifest, and atomically creates a bounded per-run
projection. The service exposes only the current approved catalog revision; runners stage verified
packages during bootstrap for the job lifetime, and old frozen locks require a verified cache or
fail without fallback. Genkit points `genkit_middleware.Skills` only at that projection. Never point
middleware at the complete extension tree or let `use_skill` bypass context-service authorization.
If middleware cannot preserve these invariants, retain the same resolved-lock contract and render
selected skills into trusted task instructions instead.

Project development also uses the checked-in `developing-genkit-python` agent skill installed from
`genkit-ai/skills`. That skill guides contributors writing Genkit code; it is not part of the hosted
runtime's team-skill catalog and grants no application capability.

Advertise relevant authorized metadata first; load full skill bodies/resources on demand and record
the resolved lock on each run. The `team-agent skills pull` interface installs named, authorized-all,
or frozen-lock selections without executing downloaded scripts. Use project-scoped generic, Codex,
and Claude adapters; required runner installation completes before harness launch. Script execution,
when explicitly authorized later, happens only in coding containers and never during installation.

Add a compact authorized baseline to each turn: scope, essential conventions, and selected approved memories. Retrieve additional knowledge through tools, preserving citations and distinguishing source content from trusted instructions. Cap context size.

## 5. Sessions, history, and shared memory

One Slack thread maps to one conversation under a unique `(workspace_id, channel_id, thread_ts)` key. Serialize turns across replicas. Follow-ups continue the conversation when earlier work submitted jobs or requested clarification.

Implement `MongoSessionStore` against the pinned Python `SessionStore` contract. Current docs expose snapshot reads and atomic functional snapshot updates, with built-in memory, file, and Firestore implementations. MongoDB support here is our custom adapter; Firestore is not required.

Keep Genkit session/snapshot identifiers mapped to application conversation IDs. Test snapshot retrieval by session or snapshot ID, branch/parent relationships, latest pointers, atomic updates, status transitions, and any abort/detach hooks actually used. Persist adapter/schema versions and isolate store keys by tenant and agent role.

Use MongoDB compare-and-set revisions or transactions for atomic updates, plus conversation leases and fencing tokens. A process-local lock alone is insufficient. Avoid unbounded snapshot documents: use a tested checkpoint/diff or chunked representation with bounded document sizes, and compact conversation context. Do not assume a snapshot ID means external tools completed.

If using the flows fallback, keep application-owned ordered messages and summaries behind the same runtime interface. Do not maintain two independent authoritative conversation histories. Optional transcripts are audit projections.

Keep three concepts separate:

1. Conversation history and Genkit state: `agent_runtime`.
2. Workflow and external action progress: `agent_runtime`.
3. Shared team memory: `team_context`, accessed through the context service.

Shared memories have scope, provenance, evidence, author, timestamps, authority, expiration, and supersession. New memories start as proposals; authorized application operations approve them. Model confidence never automatically promotes a proposal.

## 6. Portable extension kit and RAG

Keep local Codex/Claude configuration project scoped. Do not globally expose company tools in unrelated personal repositories. Local agents access MCP or the context CLI without needing Genkit or database credentials.

Context REST endpoints:

```text
POST /v1/knowledge/search
GET  /v1/documents/{id}
GET  /v1/skills
GET  /v1/skills/{name}
POST /v1/skills:resolve
GET  /v1/skill-packages/{package_id}
POST /v1/memories/search
POST /v1/memory-proposals
POST /v1/memory-proposals/{id}/approve
POST /v1/tools/{tool-name}:invoke
GET  /health
GET  /ready
```

MCP exposes the same narrow operations. The hosted runtime uses scoped REST-backed function tools initially; a verified Python MCP client integration may be added later without duplicating business logic. Do not assume Genkit's developer MCP server is the runtime context server.

Ingestion reads approved sources at pinned Git revisions, normalizes and chunks them, attaches authorization and provenance, generates embeddings, and records revision/index status. Retrieval applies access filters before returning content, combines lexical and vector results, optionally reranks, prefers approved/current sources, and returns citations. Use MongoDB Search and Vector Search where supported. Preserve a `KnowledgeService` interface if the cluster requires another search implementation.

Each returned chunk includes source ID, repository/path, revision, authority, owner, project/access groups, update timestamp, and citation reference. Treat repository content and retrieved documents as untrusted input rather than tool authorization.

## 7. Durable run and action execution

Team-agent process modes:

```text
team-agent serve
team-agent scheduler
team-agent worker
```

Ingress verifies Slack signatures, deduplicates events, acknowledges promptly, and enqueues work. The scheduler claims due occurrences. Workers claim runs with renewable leases. These modes may share a development process but must not require co-location.

Run states: `queued`, `running`, `waiting_for_jobs`, `needs_input`, `awaiting_approval`, `completed`, `failed`, `cancelled`, `timed_out`.

Persist step transitions and external action intents before invocation. Native agent background execution is optional; required recovery must work from application records even when a framework background task cannot resume. Recovery reconciles submitted actions with executor/provider state rather than blindly replaying tool calls. Use idempotency keys for job submission, review publication, branch/PR creation, and Slack reactions. A network timeout may mean an action succeeded remotely; query its status before retrying. Do not claim exactly-once delivery; provide effectively-once visible outcomes through deduplication and reconciliation.

A coding-job submission returns a job ID promptly. Suspend the logical run, let workers track completion, and invoke the coordinator with a structured result when ready. Do not hold an HTTP request open for a 30-minute coding task or poll indefinitely inside the model loop.

Genkit agent/tool calls handle reasoning and delegation; explicit Python workflow code defines mandatory order and joins. Neither replaces durable queues, scheduler leases, action records, or restart recovery.

## 8. Bounded multi-agent workflows

Start with one coordinator. Specialist agents are introduced only for an explicit workflow; each has a purpose, context budget, allowed tools, and structured output. Cap agent count, depth, model turns, total spend, time, and job concurrency. No recursive self-spawning.

### Specialist roles

Use a coordinator for task routing, an optional domain specialist for cited knowledge, external coding specialists for repository operations, and a review reconciler for evidence synthesis. Each native specialist can have its own model configuration, prompt, and tools. Do not create a specialist when one tool suffices.

Invoke fixed workflow branches through application code; use bounded asyncio concurrency for short in-process operations and durable child jobs for repository work. An in-memory asyncio task is not a recoverable job. Store branch completion and the join decision before continuing.

### Diagnose and fix

1. Coordinator retrieves incident details and relevant knowledge; loads `diagnose-and-fix`.
2. If needed, submit a pinned repository investigation/fix job.
3. Coding harness diagnoses, edits, and tests in the disposable workspace.
4. Supervisor independently validates the diff and mandatory checks.
5. Where authorized, publish a draft PR and return evidence to the Slack thread.
6. Submit useful lessons as memory proposals.

### Parallel merge-request review

Implement a fixed graph with these steps:

1. Resolve repository, base SHA, and head SHA; freeze the review target.
2. Retrieve relevant conventions and authorized domain context.
3. Launch at most two independent reviewers: correctness and security. Use separate coding jobs/workspaces with edits disabled. Review roles may use Codex or Claude through the harness interface; two different providers are not required initially.
4. Collect structured findings. Reconcile duplicates, unsupported assertions, conflicts, and severity; preserve each finding's evidence and attribution.
5. Validate target revision and findings before publication. If the head changed, mark results stale and schedule the new revision; do not publish as if the old review covers the new head.
6. Publish one consolidated review, then project completion to Slack.

The reconciler can be a Genkit specialist, but deterministic code enforces publication gates. Reviewer failures produce an explicit partial/failed result; never label an incomplete required review completed. Completion means review finished, not that the code is approved. Configure findings/status separately from completion reactions.

For future parallel edits, give each job an isolated checkout and branch. An explicit integration job resolves patches and reruns checks. Do not have multiple writers edit the same checkout or push the same branch.

## 9. Coding agent adapters and disposable execution

```python
class CodingHarness(Protocol):
    async def execute(self, context: CodingContext) -> HarnessResult: ...

class CodingJobExecutor(Protocol):
    async def start(self, request: CodingJobRequest) -> JobHandle: ...
    async def status(self, job_id: str) -> JobStatus: ...
    async def cancel(self, job_id: str) -> None: ...
    async def result(self, job_id: str) -> CodingJobResult: ...
```

Implement `LocalDockerExecutor`, `MockCodingHarness`, and then `CodexHarness`. Add `ClaudeHarness` after the first real path passes acceptance tests.

- Codex: Python invokes the pinned supported noninteractive CLI using an async subprocess and structured event output. Its documented TypeScript SDK wraps that CLI; do not invent a Python Codex SDK or add a Node service solely to use the wrapper.
- Claude: invoke the official Python Claude Agent SDK and consume its result/progress stream. Configure project settings, permissions, MCP, and session loading explicitly against the installed version.
- Normalize both into the same domain contracts. Harness sessions and Genkit sessions are separate; save their mapping and versioned session artifacts.

SDK invocation and container isolation coexist: invoke the SDK/CLI inside the coding container. A direct provider model call is not the corresponding coding harness.

### Container lifecycle

The coordinator, scheduler, and executor worker are persistent processes. One coding job receives one fresh container from a reusable image and one checkout pinned to its requested revisions. Build the image ahead of time; do not rebuild it for every task.

A job can contain many model turns, edits, commands, and test runs. Do not create a container per model call, tool call, or Slack message. A parallel review creates one job/container for each independent reviewer.

The executor creates the container using the persisted job ID as a stable label, mounts job-specific input/output locations, applies policy, starts it, captures progress, and stores artifacts before cleanup. Reconcile Docker labels after a worker restart. A crash between creation and recording must recover the existing container, not launch a duplicate.

Default to removing a terminal container once artifacts are durably saved. Follow-ups create a fresh job restored from the saved commit/patch and compatible harness session data. Optional short retention of an active task environment can be configured later, with an idle deadline; it is not required for Slack conversation continuity.

Cache downloads/dependencies only through scoped, controlled caches. Do not share writable checkouts, credentials, or harness settings between jobs. Define `ExecutionEnvironment` behind the executor so another sandbox/VM provider or a development-only persistent worker can be added without coordinator changes.

### Completion and resume

The harness adapter detects a terminal SDK result or CLI turn event and process outcome, then validates its structured output. Turn completion alone is not job success. The supervisor independently checks the resulting diff and required tests.

The executor commits terminal job state and a completion notification to MongoDB atomically (or through an idempotent outbox protocol). A run worker consumes that notification, fences concurrent conversation updates, and invokes Genkit with the validated result. MongoDB polling is the required portable baseline; change streams are optional when the deployment supports them.

Deduplicate notifications by job/attempt/result version. Enforce deadlines and cancellation through the executor, and terminate remaining subprocesses. Persist incremental progress for CLI/Slack status without sending every token to Slack.

If a result requires input or authorization, save a resumable checkpoint and transition the logical run accordingly. Persist artifacts before destroying its workspace. Retrying a job uses a new attempt and does not republish an already completed external action.

Every request/result is schema-validated and versioned. A request includes `job_id`, `run_id`,
submission idempotency key, repository, base/head revisions, objective, mode (`investigate`, `fix`,
`review`, `integrate`), harness, an immutable skill lock, pinned content revision, context references,
and supervisor-owned capability policy. Policies include allowed repositories/paths/commands,
may-edit/publish flags, timeout, diff limits, and resource limits.

Results include terminal status, diagnosis, evidence, findings, patch reference, changed files, harness-reported checks, independent verification, exact reviewed SHA, publication outcome, usage, and memory proposals. Findings include severity, path/line reference, explanation, evidence, and suggested action.

Terminal job states: `completed`, `failed`, `timed_out`, `cancelled`, `needs_input`, `awaiting_approval`, `no_fix_found`, `unsafe_to_proceed`.

The supervisor creates an ephemeral workspace, checks out exact revisions, configures project instructions and the extension, grants scoped credentials, launches the harness, validates output/diff, executes mandatory checks, and performs authorized publication. Keep Git write credentials out of model context and harness environment. Verification must use an execution environment with no publication credentials; repository tests are arbitrary code.

## 10. Automations

Event-triggered and scheduled definitions create the same workflow runs as manual requests. Deterministically parse and allowlist MR URLs; do not ask a model to decide whether a URL is an authorized repository.

Automation definitions select named skills and may require a catalog revision. Resolve that
selection when creating a workflow run and persist the immutable lock. Same-job retries reuse staged
packages. A new job may satisfy an old lock from a verified cache; otherwise unavailable exact
packages fail rather than substituting a newer deployment. The automation service uses the shared
Python resolver, and runner bootstrap consumes the same frozen lock.

Support the original use cases:

- A new message in a configured Slack channel linking an MR triggers a review and completion reaction after publication succeeds.
- Every six hours, scan a bounded channel window and reconcile unreviewed revisions or missing reactions.

Logical review identity:

```text
automation_id + slack_message_id + merge_request_id + head_sha
```

Use this for message-specific dispatch/projection. Also maintain a canonical review key `(workflow_version, repository, merge_request_id, head_sha, review_policy_fingerprint)` to reuse an authorized equivalent review across duplicate messages where configured. Do not reuse results across different access scopes or review requirements.

Unique scheduled occurrence key: `(automation_id, scheduled_occurrence)`. Atomically claim due definitions, create occurrences, track child runs, and advance scheduling state without losing work on crash. Use transactions where necessary, or a documented idempotent recovery protocol. Store timezone, schedule expression, next-run timestamp, cursor, enabled state, creator, policy, budgets, and concurrency limits.

Emoji is a projection, not the authority. Internal review records establish completion for an exact SHA. Reconciliation repairs missing reactions without rerunning completed reviews. A new SHA gets a new review. Cursor advancement must retain failed/deferred targets in a durable retry ledger. Use bounded backoff and inspectable exhausted failures.

## 11. MongoDB collections and indexes

`agent_runtime`: identities, slack_threads, sessions, session_snapshots, snapshot_chunks, session_pointers, session_events, runs, workflow_steps, coding_jobs, completion_outbox, action_intents, approvals, event_receipts, automations, automation_runs, code_reviews, review_projections.

`team_context`: documents, document_chunks, source_revisions, memories, memory_proposals, audit_events.

Skill packages and discovery metadata are rebuildable from Git and are not MongoDB collections.
Runtime and automation records retain only the selected identities, immutable revisions, and hashes
needed for provenance and retry.

Only the context service accesses `team_context`. Local agents receive no MongoDB credentials. Use collection validators and explicit schema migrations.

Required unique indexes: Slack thread identity; Slack event receipt ID; session event ID and `(session_id, sequence)` where events are used; tenant/agent/snapshot ID and session pointer key; coding submission key; action idempotency key; automation occurrence key; canonical review key; message-specific projection key. Add query indexes for status/next-at/lease expiration and scope/source revision. Apply TTL only to disposable receipts or expired data with a defined retention policy, not to authoritative completion records needed for deduplication.

Avoid embedding unbounded events, logs, or findings in one document. Keep large patches/logs out of model prompts; use bounded extracts and artifact references. Local dev may use a Docker volume for artifacts; define an artifact-store interface for other environments.

## 12. Authorization and operational controls

Authenticate humans and services, map Slack identities to company scope, and filter knowledge before retrieval returns. Enforce tools and workflows outside prompts. Approvals are bound to run/action, repository, revision, capabilities, and expiration; changes invalidate incompatible approvals.

Explicit requests to create a PR authorize a draft PR within the allowed scope. Automation creation records the permitted action classes. Merge, deploy, production mutation, and data migration require separate authority and are disabled in the initial release.

Do not mount a Docker socket into Slack ingress or the coordinator in production. Run the local Docker executor in a separate trusted worker process; make the boundary explicit in Compose and docs. Containers run non-root, use ephemeral workspaces, have resource/time limits, and emit structured logs. Network isolation is enforced by the executor/platform, not by prompt text.

Record correlations across event, conversation, workflow, agent invocation, coding job, reviewed commit, publication, and reaction. Redact secrets and sensitive incident fields. Configure tracing/export deliberately; avoid sending full internal payloads to external telemetry by default.

## 13. Configuration

Use service-specific environment variables for MongoDB credentials/database, context URL, Slack secrets, process mode, model provider/ID/endpoint, content revision/path, embedding provider, executor, harness, retention, budgets, and telemetry. Keep coordinator credentials separate from coding-harness credentials. Never assume a ChatGPT/Claude subscription supplies API credentials or rights for unattended service use.

Provide `.env.example` with placeholders and safe mock mode. Default local demos to fake Slack, mock model/runtime, fixture knowledge, and mock Git publication. Real model/harness/Git operations are explicitly configured. Docker Compose may run a single-node MongoDB replica set if chosen transactions require it.

## 14. Implementation sequence and acceptance

### Phase 0 — Python compatibility spike

Verify the pinned Genkit Python APIs and the OpenAI plugin using a small executable sample. Select
and record one exact Codex-capable OpenAI API model ID. Validate tool calls, structured outputs,
streaming, conversation continuation, custom session persistence, Skills middleware, abort behavior,
FastAPI integration, and available specialist delegation. Record feature support and choose Agents
API or flows fallback. Keep live API checks opt-in; deterministic fakes must let later application
work proceed without credentials. Test the actual Codex noninteractive command and Claude Python SDK
interface when installed.

Exit: a checked-in compatibility matrix and runnable sample establish supported imports, signatures,
the configured OpenAI model ID, and the authorized skill-loading path. Missing credentials are
documented; mocks allow application work to proceed without claiming live compatibility.

### Phase 1 — Foundation and extension

Create the Python workspace, contracts, mock clients, MongoDB knowledge repositories/indexes,
context REST service, development Compose, image targets, and fixture knowledge/skills. Implement
authenticated cited retrieval, the Git-backed in-memory skill catalog, immutable package resolution,
verified pull/lock installation, memory proposals, MCP/context CLI, and project-scoped local-agent
adapters.

Exit: all image targets build; authorized REST/MCP/CLI retrieval agrees; targeted/all/frozen pulls
are atomic and reproducible; Codex/Claude project layouts pass discovery fixtures; denied scope never
returns chunks or packages; knowledge revisions, skill locks, and memory provenance are preserved.

### Phase 2 — Genkit coordinator and conversations

Implement model factory, Genkit adapter consuming the shared skill resolver/installer, MongoDB session
store, provider contract suite, and runtime CLI. Use mock runtime fixtures in CI, then validate one
live provider when credentials exist.

Exit: skill-guided retrieval works; multi-turn state survives restart; concurrent same-thread turns are serialized; live-provider limitations are documented without silent parameter dropping.

### Phase 3 — Coding vertical slice

Implement leased runs/jobs, immutable skill locks as job inputs, frozen runner-bootstrap installation,
local Docker executor, mock then real Codex harness, independent checks, cancellation/timeouts, and
mock draft PR publication.

Exit: `diagnose-and-fix` fixes a fixture idempotency defect using a retrieved ADR, passes its regression test and supervisor checks, and returns a structured draft PR result. A crash after submission does not create a duplicate job or publication.

### Phase 4 — Slack and automations

Add signed Slack ingress, identity/thread mapping, asynchronous replies, URL validation, event automation, scheduler, six-hour reconciliation, cursors, and reaction projections.

Exit: duplicate events and scans review one revision once; missing reaction is repaired; new head SHA triggers a new review; failed/deferred targets survive cursor advancement; leases recover after worker failure.

### Phase 5 — Bounded multi-agent review

Implement the two-role parallel review and reconciliation workflow with separate read-only workspaces and a maximum of two jobs per review. Add global/per-automation caps. Preserve a sequential option.

Exit: duplicate/conflicting findings are reconciled with evidence; stale heads and missing required reviewers block successful publication; result attribution and budgets are visible; restarting resumes/reconciles steps rather than repeating side effects.

### Phase 6 — Real integrations and hardening

Implement configured Git provider publication, second harness, approval UX, credential rotation, injection/failure evals, and operational recovery docs. Keep deployment neutral.

Exit: end-to-end fixture and failure suites pass; real publication uses supervisor credentials only; no merge/deploy/production capability is exposed; local extension remains usable without Genkit.

## 15. Required verification

Use meaningful unit/integration tests for authorization, MongoDB session semantics, fencing, event and occurrence deduplication, action reconciliation, skill selection, memory promotion, stale-head review, partial reviewer failure, cancellation, diff rejection, credential separation, and provider translation. Include prompt-injection fixtures in retrieved knowledge and repository files.

Workflow evals assess grounded answers, valid evidence, useful diagnosis, unsupported findings, and skill relevance. Each initial skill has positive and negative trigger cases. Do not treat two agreeing model reviewers as proof of correctness; require evidence and independent executable checks where applicable.

CI uses mocks and fixture repositories without external credentials. Live provider/harness suites are opt-in and bounded by spend/time. Document what was verified versus stubbed or unavailable.

## 16. Official implementation references

Checked against current documentation on 2026-10-03. Pin and verify actual package releases before coding; documentation may describe capabilities newer than an installed package. Python is marked preview and Agents API is beta.

- Python overview: https://genkit.dev/docs/python/overview/
- Python agents: https://genkit.dev/docs/python/agents/overview/
- Agent definitions: https://genkit.dev/docs/python/agents/define/
- Session stores and custom contract: https://genkit.dev/docs/python/agents/session-stores/
- Python flows: https://genkit.dev/docs/python/flows/
- Python tools: https://genkit.dev/docs/python/tool-calling/
- Python Anthropic plugin: https://genkit.dev/docs/python/integrations/anthropic/
- Python OpenAI plugin: https://genkit.dev/docs/python/integrations/openai/
- Python API reference: https://python.api.genkit.dev/
- Multi-agent pattern reference: https://genkit.dev/docs/agents/multi-agent/ (verify language-specific support)
- Codex noninteractive execution: https://developers.openai.com/codex/noninteractive/
- Codex SDK source (TypeScript, reference only): https://github.com/openai/codex/tree/main/sdk/typescript
- Claude Agent SDK: https://code.claude.com/docs/en/agent-sdk/overview

## 17. Codex kickoff instructions

Use this document as the implementation source of truth. Implement, rather than only proposing a plan.

1. Inspect the target repository and its instructions. Preserve existing conventions where compatible.
2. Run Phase 0 to verify current Genkit Python agents, flows, tools, session-store and provider APIs. Choose pinned dependencies and document any required adjustments.
3. Create a concise file-level plan and begin Phase 1. Do not block on routine choices already made in this spec.
4. Build incrementally through the mock `diagnose-and-fix` vertical slice, with meaningful tests and runnable Compose instructions.
5. Keep application/domain interfaces free of Genkit types, retain REST/MCP/CLI parity, and keep durable execution outside the model loop.
6. Use mocks when credentials are absent. Do not claim live Opus/Sol or real Codex/Git integration passed without running it.
7. Report completed phases, verification results, remaining stubs, and exact next steps.

Do not add production orchestrator manifests, unrestricted recursive agents, automatic merges/deployments, raw shared-database tools, or a relational database dependency. Do not replace Codex/Claude Code with a homemade coding loop. Do not expose secrets in prompts, traces, fixtures, or logs. Expand to the bounded parallel review only after the single-run path is reliable.

# Architecture

## Purpose and current state

The kit lets an organization expose approved team context to local and hosted agents without making
that context globally available or embedding authorization in prompts. Genkit Python is the planned
hosted orchestration runtime, initially through an explicit OpenAI model configuration; local Codex
and Claude integrations remain independent of Genkit.

Migration proceeds as verified vertical slices. The current implementation is S04: a Python context
service that validates a Git-owned extension pack, synchronizes only its knowledge index at a pinned
revision into MongoDB, builds its skill catalog in memory, and provides authenticated cited search
plus authorized skill list/get, immutable resolution, and exact package-download operations. The pull
CLI/MCP, Genkit, coding jobs, Slack, and automation are not yet implemented.

## Runtime boundaries

Three independently runnable images define the target security and lifecycle boundaries:

1. The team agent owns ingress, identities, conversations, Genkit execution, workflow routing, and
   automation records. It cannot directly access shared-context storage or repository shells.
2. The context service owns authorized retrieval, ingestion, skills, governed team memory, narrow
   internal tools, and context audit. It does not orchestrate arbitrary agents.
3. The coding runner handles one pinned repository job in a disposable workspace. A trusted supervisor
   owns credentials, policy, independent checks, and permitted publication.

Genkit's model and agent execution is unrelated to the disposable coding runner. The OpenAI model
adapter is also distinct from the Codex CLI harness. Application code—not an agent prompt—owns
authorization, durable execution, validation, and external side effects.

## Source-of-truth boundaries

- Git owns approved knowledge, skills, conventions, and architecture decisions.
- The `team_context` MongoDB database owns derived knowledge indexes and source revision state; it
  will also own governed memories and context audit. It does not own the skill catalog.
- The `agent_runtime` MongoDB database will own conversations, Genkit snapshots/state, workflows, jobs,
  approvals, automations, reviews, and projections.
- The databases use distinct service credentials. Only the context service can access `team_context`.
- Local agents and coding runners receive no MongoDB credentials.

The in-memory knowledge index remains available for tests and mock mode. Production startup uses the
MongoDB knowledge adapter, applies strict validators/indexes, synchronizes the configured knowledge
revision, and independently builds the skill catalog from the validated Git/filesystem content pack.

## Extension and retrieval invariants

An extension pack contains a validated manifest, knowledge roots, and portable skill packages.
Every returned knowledge item carries repository, path, revision, and optional heading. Project,
group, and authority filters are applied before scoring. Repository content is untrusted input and
cannot grant tool authority.

Skill metadata must reference projects and access groups declared by the extension manifest. The Git
skill catalog is bound to the configured extension checkout, and project/group authorization is
applied before pagination and body retrieval. Absent and unauthorized names share one not-found
response. Skill bodies retain repository/path/revision provenance. The loader recursively inventories
bounded self-contained packages, rejects symlinks and unsafe or missing explicit resources, hashes
raw file bytes with bounded streaming reads, and derives provenance-bound content IDs. Resolution
authorizes names before revision errors and serves only the current catalog without substituting
another revision. Jobs later stage verified packages during bootstrap for their lifetime; an old
frozen lock requires a verified cache or fails. Lockfiles and atomic installation follow in later
distribution slices; none of these interfaces are Genkit built-ins. Python JSON contracts use
snake_case.

REST is the primary context interface. MCP and CLI will adapt the same application services and
policy decisions. Genkit-specific types stay inside `runtime_genkit`.

Git-owned team skills remain provider neutral. The shared `SkillClient` resolves an authorized subset
to an immutable lock and installs a bounded projection before the runtime points Genkit Skills
middleware at it. Coding runners use the same lock and installer with a harness target. The
checked-in `developing-genkit-python` agent skill guides repository development only; it does not
grant hosted runtime capabilities.

## Operational invariants

- Long-running processes expose liveness and dependency-aware readiness.
- The context service receives only context-database credentials. Compose proves those credentials
  cannot read `agent_runtime`; a configuration guard also rejects identical database names.
- Logs and artifacts minimize and redact sensitive values.
- Retriable external actions use durable idempotency keys and reconcile uncertain outcomes.
- Coding credentials are unavailable to model and verification environments.
- Automatic merge, deploy, production mutation, unrestricted shell, and recursive agent spawning are
  outside the kit.

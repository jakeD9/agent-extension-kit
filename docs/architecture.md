# Architecture

## Purpose

The kit lets organizations fork one repository, replace a content pack, and expose approved team context to local and hosted agents without making that context globally available or embedding policy in prompts.

## Runtime boundaries

Three independently runnable images define the security and lifecycle boundaries:

1. The Slack agent verifies Slack input, owns conversation/run state, calls models, and dispatches work. Its server, scheduler, and worker modes may be separate processes.
2. The team context service owns knowledge retrieval, skill discovery, memory governance, authorization, auditing, and allowlisted internal tools.
3. The coding runner executes one bounded job in a disposable, credential-free workspace and exits with a structured result.

The team-context CLI is a workspace binary, not a fourth image. Its MCP stdio mode adapts local agent calls to the authenticated REST service. MCP is an adapter rather than the system architecture.

## Source-of-truth boundaries

- Git owns approved knowledge, skills, conventions, and architecture decisions.
- One `agent_extension_kit` MongoDB database stores all durable state by default.
- Knowledge, skills, memories, context audits, Slack threads, agent runs, approvals, event receipts, automations, leases, and review records remain separated into purpose-specific collections.
- Services receive only the collection permissions they need. Local agents receive no MongoDB credential.

The single-database default keeps local and small-team deployments simple. Larger deployments may route the same repository interfaces to separate databases without changing agent-facing contracts.

See [MongoDB Data Model](data-model.md) for the collection catalog, ownership, and lifecycle rules.

Every returned knowledge item includes repository, path, revision, and optional heading. Authorization filters are applied before candidate text reaches the scorer or model.

## Extension packs

An extension pack contains a validated manifest, knowledge roots, and portable skill packages. A fork changes content and authorization metadata without editing framework code. Samples are fictional, removable, and carry no authority outside development.

The local index uses deterministic lexical scoring over MongoDB-backed authorized chunks. An Atlas adapter may add lexical/vector fusion behind the same knowledge-index port without changing REST, CLI, or MCP contracts.

## Trust and execution

The model-facing harness never receives a Git write credential. A trusted executor supplies a credential-free workspace, receives a patch and structured result, recreates the patch in a clean checkout, enforces diff policy, runs independent checks, and only then publishes a namespaced branch or draft pull request.

Local Docker execution is for development. Production systems provide another job-executor adapter and must not mount the Docker socket into an internet-facing Slack process.

Retrieved documents and repositories are untrusted input. Tool descriptions, application services, and execution policy—not prompts alone—enforce authorization, limits, and prohibited actions.

## API rules

The service exposes additive `/v1` contracts. It uses bearer authentication, cursor pagination for lists, machine-readable error codes, and request IDs. Retriable writes require idempotency keys. Consequential operations use explicit action endpoints and audit the resolved principal, policy, input digest, outcome, and run ID.

Jobs have active (`queued`, `running`), suspended (`needs_input`, `awaiting_approval`), and terminal states. Resume and cancel are idempotent and validated against the state machine.

## Operational invariants

- Long-running processes expose liveness and dependency-aware readiness endpoints.
- Logs are structured and redact secrets and sensitive payloads.
- Slack events, scheduled occurrences, coding jobs, and review revisions are deduplicated by durable keys.
- Exhausted work becomes an inspectable failure; it never retries indefinitely.
- Automatic merge, deploy, production mutation, and unrestricted shell access are outside the kit.

# S08 Summary — Governed Team Memory

## Delivered

- Added strict snake_case contracts for sourced proposals, approver decisions, authoritative
  memory, expiry, supersession, bounded cursor search, immutable audit records, and idempotent
  mutation receipts.
- Added project- and group-scoped lifecycle enforcement. Proposal authors cannot promote records;
  only principals with the `approver` role and authority over every protected group may approve,
  reject, or expire them. Hidden and missing records remain indistinguishable.
- Exposed proposal and authorized search through REST, JSON CLI, and MCP. Governance and audit
  operations remain REST/CLI-only so models cannot promote or inspect audit history directly.
- Added exact idempotent replay, request-fingerprint conflict detection, expected-revision compare
  and set behavior, expiration, supersession, provenance/evidence retention, and append-only audit.
- Added targeted, authorization-filtered MongoDB queries and transactionally atomic lifecycle
  writes. Strict v4 validators and indexes mirror the shared contract, including nested citation
  bounds and evidence cardinality.
- Upgraded local MongoDB to an authenticated single-node replica set with generated keyfile,
  idempotent initialization, writable-primary health, and a real transactional readiness probe.

## Verification evidence

- The final deterministic suite passed 192 tests with three explicitly opt-in tests skipped.
- Ruff lint/format, strict mypy, lock consistency, `git diff --check`, Compose validation, strict
  MCP Inspector discovery, and builds for all eight Python distributions passed.
- The opt-in real MongoDB integration passed transaction/restart persistence, authorization
  separation, concurrent approve-versus-reject CAS, exact idempotent replay, simultaneous
  same-key same/different-payload races, and malformed durable-record rejection.
- The rebuilt Compose stack reported healthy MongoDB and context services; `/ready` reported the
  durable backend and the pinned local source revision ready.
- Independent review findings were fixed and rechecked: full-group mutation scope, timezone-aware
  expiry, bounded citation input, host replica-set connectivity, concurrent idempotency, and
  Pydantic/Mongo validator parity.

## S09 inputs and boundaries

- Authoritative memory means approved, unexpired, and unsuperseded records visible to the caller;
  the coordinator must use the shared client rather than access MongoDB directly.
- MCP intentionally exposes only `propose_team_memory` and `search_team_memory`. Promotion,
  expiry, rejection, and audit stay outside the model-facing tool surface.
- Search is bounded lexical ranking over at most 10,000 already-authorized/current candidates.
  Vector retrieval and reranking remain later work.
- Proposal expiry is enforced at decision time without rewriting a proposal merely because an
  approval attempt failed. Explicit expiry applies to approved memory.
- Conversation/session state remains excluded. S09 owns the stateless coordinator tracer; S10
  owns durable Genkit conversations.

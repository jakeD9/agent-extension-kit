# S02 Summary — Persistent Context Boundary

## Delivered

- Added the official PyMongo async driver and a dedicated Python database package behind the
  existing provider-neutral `KnowledgeIndex` contract.
- Added explicit, idempotent MongoDB migrations with strict validators and scope/revision indexes
  for documents, chunks, source revisions, and schema migration records.
- Added pinned Git revision synchronization with provenance-preserving upserts, stale-content
  pruning, ready/failed status records, and dependency-aware readiness recovery.
- Wired production REST startup to MongoDB while retaining the in-memory adapter for tests and mock
  mode; the public search and citation contracts are unchanged.
- Added separate Compose users for `team_context` and `agent_runtime`; the context container receives
  no runtime credential and rejects identical context/runtime database configuration.
- Added an opt-in real-Mongo integration test covering migration, restart via a new client,
  authorization, citation persistence, and denied runtime-database access.

## Verification evidence

- The default suite passed 15 tests; the live MongoDB integration test passed 1 test against MongoDB
  8.0 using the context-service credential.
- Ruff lint/format, strict mypy, lockfile consistency, and builds for all five Python distributions
  passed.
- The `team-context` image built and ran as UID 10001, reported Mongo/revision readiness, and returned
  the expected `local-example` citation before and after a process restart.
- A direct `agent_runtime.sessions` read authenticated as `team_context_service` failed with MongoDB
  `not authorized` and exit code 1.
- No Genkit session/runtime records, memory APIs, CLI/MCP parity, or live model/provider behavior was
  implemented or claimed.

## S03 inputs and constraints

- Reuse the application service for REST, CLI, and MCP parity; Mongo remains the knowledge index,
  while skills are rebuilt from the pinned Git/filesystem content pack.
- Port skill metadata/body retrieval and project-scoped local-agent configuration before removing
  the TypeScript parity implementation.
- Remove the Node workspace only after parity and negative-authorization evidence is fresh; Git
  history is the recovery path.
- Keep governed memory and all Genkit/runtime records out of S03.

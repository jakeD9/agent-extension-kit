# MongoDB Data Model

## Status

S03 keeps persistent knowledge in `team_context` while rebuilding the authorized skill catalog from
the pinned Git/filesystem content pack. Collections marked S02 have strict JSON Schema validators and
migration-owned indexes; later collections remain target design.

## `team_context`

Owned exclusively by the context service:

| Collection | Purpose |
|---|---|
| `documents` | **S02.** Git source metadata, ownership, authority, and current revision. |
| `document_chunks` | **S02.** Authorized searchable chunks with revision-pinned citations. |
| `source_revisions` | **S02.** Synchronization status, pinned revision, count, and failure state. |
| `schema_migrations` | **S02.** Applied context schema versions. |
| `memories` | Approved, scoped memories with provenance, expiry, and supersession. |
| `memory_proposals` | Candidate memories and authorized review history. |
| `audit_events` | Append-only context authorization and mutation decisions. |

## `agent_runtime`

Owned by the team-agent control plane and never accessed by the context service:

| Collection | Purpose |
|---|---|
| `identities`, `slack_threads` | Company identity resolution and durable thread mapping. |
| `sessions`, `session_snapshots`, `snapshot_chunks`, `session_pointers` | Genkit conversation metadata, bounded snapshot lineage, chunked state, and latest pointers. |
| `session_events` | Optional ordered application events when required by the pinned session adapter; not a second authoritative history. |
| `runs`, `workflow_steps` | Durable reasoning/workflow progress and limits. |
| `coding_jobs`, `action_intents` | External job and side-effect recovery records. |
| `approvals`, `event_receipts` | Capability-bound decisions and ingress deduplication. |
| `automations`, `automation_runs` | Definitions, occurrences, leases, cursors, and retry state. |
| `code_reviews`, `review_projections` | Exact-revision review authority and Slack projection state. |

## Shared requirements

Durable records have stable identifiers, creation/update timestamps, schema versions, and relevant
project/run correlation. Retriable operations include idempotency keys; leased work includes owner,
expiry, and fencing token. Unbounded events, logs, findings, and patches are stored separately or by
artifact reference rather than embedded into one growing document.

Validators, migrations, and indexes are introduced by the first slice that writes each collection.
TTL applies only to disposable records with defined retention, never authoritative completion records
needed for deduplication or audit.

S02 indexes `document_chunks` by project, authority, access group, and source before application
scoring, plus source/revision for provenance inspection. `(source_id, revision)` is unique in
`source_revisions`. Synchronization is idempotent, prunes stale source documents/chunks, records
failed attempts, and is retried during readiness recovery.

Skill packages are not a MongoDB collection. The context service validates the pinned Git checkout
at startup, builds an in-memory authorized catalog, and can rebuild it without database state. Mongo
source-revision readiness records knowledge synchronization only. A deployment upgraded from the
superseded S03 design may retain an unused legacy `skills` collection until an operator removes it;
the service no longer creates, queries, synchronizes, or requires that collection. The v3 migration
upgrades every historical source-revision row and removes its obsolete `skill_count` field before
recording the migration.

# MongoDB Data Model

## Status

The corrected S08 implementation keeps persistent knowledge projections and supplemental shared
working memory in `team_context` while rebuilding the complete domain skill catalog from the same
pinned Git/filesystem content pack. Its v5 validators remove per-artifact groups, proposals,
approvals, and canonical-memory claims.

## `team_context`

Owned exclusively by the context service:

| Collection | Purpose |
|---|---|
| `knowledge_documents` | Rebuildable Git source metadata staged under an exact repository revision. |
| `knowledge_chunks` | Rebuildable searchable chunks scoped to one exact revision. |
| `knowledge_revisions` | Staged projection status, exact Git revision, deterministic projection hash, expected counts, and readiness. |
| `active_knowledge_revisions` | Atomic project/domain pointer to one verified revision and projection hash. |
| `schema_migrations` | **S02.** Applied context schema versions. |
| `shared_memories` | Team-wide supplemental working memory with provenance, revision, expiry, and supersession. |
| `shared_memory_audit_events` | Append-only working-memory lifecycle events. |
| `shared_memory_idempotency` | Durable request fingerprints and exact mutation replay results. |

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

Knowledge projection queries are bounded by project/domain and exact source revision before
application scoring. Publication stages records under an immutable build identity, verifies
completeness, then atomically compare-and-sets the active pointer. Readers observe one complete
revision and projection hash. Failed and stale concurrent builds cannot replace the current
revision. If an activation acknowledgement and its reconciliation read both fail, the candidate is
recorded as `activation_uncertain`, never falsely failed; retry reconciles the exact staged hash and
count. The projection is rebuildable from Git under the contract in
`required-ci-implementations.md`.

Skill packages are not a MongoDB collection. The context service validates the pinned Git checkout
at startup, builds an in-memory domain catalog, and can rebuild it without database state. Mongo
source-revision readiness records knowledge synchronization only. A deployment upgraded from the
superseded S03 design may retain an unused legacy `skills` collection until an operator removes it;
the service no longer creates, queries, synchronizes, or requires that collection. Legacy
`documents`, `document_chunks`, and `source_revisions` collections likewise remain untouched and
unused. Migration v5 creates the revision-scoped `knowledge_*` projection collections instead of
rewriting or reinterpreting historical projection rows.

The superseded v4 migration introduced proposal/approval records. Migration v5 registers the new
collections and fails readiness when legacy approval-era collections contain data; it does not
delete or reinterpret those records. Working-memory transactions commit create/update/expiry,
optional supersession, audit, and idempotency together. Search first checks project/domain admission, then returns current,
unexpired, unsuperseded supplemental records without per-record audiences. Audit events have no TTL
and are inserted only. Development Compose runs MongoDB as a single-node replica set; readiness
proves a session-bound transactional write rather than a ping.

Immutable package manifests and bundles are likewise derived in memory from the pinned checkout.
Their content IDs bind repository and source-revision provenance, package metadata, and the complete
sorted file inventory. The service serves the current canonical Git revision only. Later runtime/job
records persist snake_case locks and stage verified packages for a job lifetime; an unavailable old
package requires a verified cache or fails rather than selecting current content.

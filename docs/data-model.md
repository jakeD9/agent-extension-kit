# MongoDB Data Model

## Status

S08 keeps persistent knowledge projections and supplemental shared working memory in `team_context`
while rebuilding the complete domain skill catalog from the pinned Git/filesystem content pack.
S10 adds application-owned durable conversations to the separate `agent_runtime` database. Genkit
receives bounded context but does not own a session store or another transcript.

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
| `conversations` | Current S10 conversation summary, compaction cursor, and minimal active-turn claim. |
| `conversation_turns` | Current S10 ordered requests and complete results, including citations, usage, content revision, and skill lock. |
| `schema_migrations` | Applied runtime-database schema versions. |
| `identities`, `slack_threads`, `runs`, `coding_jobs`, `event_receipts`, `automations`, `automation_runs`, `code_reviews`, `review_projections` | Planned collections created only by the later slice that first writes them. |

### `conversations` fields

| Field | Purpose | Example |
|---|---|---|
| `_id` | Stable application conversation ID. Slack mapping is added later. | `conversation-42` |
| `schema_version` | Document-shape version validated by MongoDB. | `"1"` |
| `project` | Trusted project/domain boundary; it cannot change within a conversation. | `platform` |
| `summary` | Deterministic text extracted from compacted completed turns, capped at 12,000 characters. | `Turn 1\nUser: ...\nAssistant: ...` |
| `summary_through_sequence` | Highest completed turn sequence already represented in `summary`. It is also the compaction compare-and-set cursor. | `4` |
| `active_turn_id` | Run ID currently allowed to complete, or `null`. | `run-9` |
| `claim_generation` | Monotonic generation incremented on every claim/reclaim. Completion must match it, which rejects a stale worker after expiry. It is not a secret token. | `9` |
| `claim_expires_at` | Recovery deadline for an abandoned active turn, or `null` when idle. V1 does not renew it in a worker heartbeat loop. | `2026-10-05T15:01:00Z` |
| `created_at`, `updated_at` | Creation and last claim/result mutation times. | `2026-10-05T15:00:00Z` |

Sample idle conversation after compaction:

```json
{
  "_id": "conversation-42",
  "schema_version": "1",
  "project": "platform",
  "summary": "Turn 1\nUser: Recover the worker\nAssistant: Restarted it safely.",
  "summary_through_sequence": 1,
  "active_turn_id": null,
  "claim_generation": 7,
  "claim_expires_at": null,
  "created_at": "2026-10-05T15:00:00Z",
  "updated_at": "2026-10-05T15:04:00Z"
}
```

### `conversation_turns` fields

| Field | Purpose | Example |
|---|---|---|
| `_id` | Deterministic storage ID derived from conversation and run IDs. | `conversation-42:run-7` |
| `schema_version` | Document-shape version validated by MongoDB. | `"1"` |
| `conversation_id` | Parent conversation. | `conversation-42` |
| `run_id` | Caller idempotency ID for this logical turn. | `run-7` |
| `sequence` | Stable ordering number assigned on the first claim. A reclaimed turn retains its sequence. | `7` |
| `project` | Duplicated project/domain boundary for direct validation and diagnostics. | `platform` |
| `request_fingerprint` | SHA-256 of the caller-supplied request without injected history. The same run ID replays the stored result only when this fingerprint matches. | `sha256:8d...` |
| `request` | Original provider-neutral `AgentTurnRequest`; `conversation_context` is stored as `null` because history is reconstructed by the application. | `{"run_id":"run-7", ...}` |
| `result` | Complete `AgentTurnResult`, or `null` while active. It preserves text, citations, decisions, usage, failures, exact content revision, and exact selected skill lock. | `{"status":"completed", ...}` |
| `status` | Persistence lifecycle: `active`, `completed`, or `failed`. | `completed` |
| `claim_generation` | Generation that may write this result. It changes when an expired turn is reclaimed. | `7` |
| `claim_expires_at` | Deadline recorded for the claim attempt, retained for audit/debugging. | `2026-10-05T15:04:00Z` |
| `created_at`, `completed_at` | First-claim time and terminal persistence time; `completed_at` is `null` while active. | `2026-10-05T15:03:05Z` |

Sample completed turn (the lock is shortened only for readability):

```json
{
  "_id": "conversation-42:run-7",
  "schema_version": "1",
  "conversation_id": "conversation-42",
  "run_id": "run-7",
  "sequence": 7,
  "project": "platform",
  "request_fingerprint": "sha256:8d0f...",
  "request": {
    "schema_version": "1",
    "run_id": "run-7",
    "project": "platform",
    "objective": "Did the worker recover?",
    "skill_names": ["incident-guide"],
    "conversation_context": null
  },
  "result": {
    "schema_version": "1",
    "run_id": "run-7",
    "status": "completed",
    "text": "The health check is passing.",
    "canonical_knowledge_citations": [
      {"repository": "company/runbooks", "path": "worker.md", "revision": "abc123", "heading": "Recovery"}
    ],
    "supplemental_memory_citations": [],
    "content_revision": "abc123",
    "selected_skill_lock": {
      "schema_version": "1",
      "project": "platform",
      "target": "generic",
      "catalog_revision": "abc123",
      "packages": [
        {
          "schema_version": "1",
          "package_id": "sha256:aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa",
          "name": "incident-guide",
          "description": "Recover workers safely.",
          "version": "1.0.0",
          "source_revision": "abc123",
          "files": [
            {"path": "SKILL.md", "sha256": "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb", "size": 512}
          ],
          "resources": [],
          "citation": {"repository": "company/extensions", "path": "skills/incident-guide/SKILL.md", "revision": "abc123", "heading": null}
        }
      ]
    },
    "decisions": [
      {"kind": "source_precedence", "name": "canonical_knowledge_over_memory", "detail": "Git-backed knowledge is canonical."}
    ],
    "usage": {"source": "observed", "input_tokens": 800, "output_tokens": 90, "total_tokens": 890, "cached_content_tokens": 0, "thoughts_tokens": 0},
    "failures": []
  },
  "status": "completed",
  "claim_generation": 7,
  "claim_expires_at": "2026-10-05T15:04:00Z",
  "created_at": "2026-10-05T15:03:00Z",
  "completed_at": "2026-10-05T15:03:05Z"
}
```

Stored documents contain the exact validated skill lock. A request/result pair is limited to 8 MiB
before persistence, leaving headroom below MongoDB's 16 MiB BSON document limit.

### Turn and compaction behavior

V1 permits one active coordinator-worker process. A process-local lock serializes each conversation
while allowing different conversations to run concurrently. The persisted claim fields recover a
crash and reject a late completion; they are not a distributed worker scheduler. There is no
`lease_owner`, `lease_token`, renewable heartbeat, snapshot ancestry, or Genkit session record.

Mongo transactions make claim acquisition atomic with active-turn insertion, and terminal-result
persistence atomic with claim release. An ordinary task cancellation best-effort stores a bounded
`turn_cancelled` failure before propagating cancellation. Process death is recovered by claim expiry;
a later claim marks an abandoned active turn `claim_expired` and its old generation cannot complete.

Each model request contains at most the 12,000-character summary plus six recent completed turns,
with a 48,000-character aggregate conversation-context limit. Mongo reads at most seven completed
turn candidates at a time. Deterministic compaction moves the oldest excess completed turn into the
summary and advances `summary_through_sequence`. Failed turns remain durable for diagnosis but do
not enter subsequent model context.

Indexes enforce unique `(conversation_id, sequence)` ordering and unique
`(conversation_id, run_id)` idempotency. A non-unique `(conversation_id, status, sequence)` index
keeps completed-turn context and compaction scans bounded even when many failed turns exist. The
conversation claim-expiry index supports operational recovery queries.

### Runtime schema changes

Startup migration creates missing collections, applies strict validators with `collMod`, creates the
required indexes idempotently, and records `runtime-schema-v1` in `schema_migrations`. The
`schema_version` inside each document identifies that document's validated shape; the migration
record identifies which database-level setup has run. A later incompatible shape requires an
explicit v2 migration and data transition before changing the validator—it is not inferred from the
Pydantic model and old records are never silently reinterpreted.

## Shared requirements

Durable records have stable identifiers, creation/update timestamps, schema versions, and relevant
project/run correlation. Retriable operations include idempotency keys. Each later work queue must
document only the recovery fields it actually needs; conversation claims deliberately use an expiry
and monotonic generation without an owner or opaque token. Unbounded events, logs, findings, and
patches are stored separately or by artifact reference rather than embedded into one growing
document.

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

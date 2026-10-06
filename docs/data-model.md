# MongoDB Data Model

## Status

S08 keeps persistent knowledge projections and supplemental shared working memory in `team_context`
while rebuilding the complete domain skill catalog from the pinned Git/filesystem content pack.
S10 adds application-owned durable conversations to the separate `agent_runtime` database. S11 adds
minimal runs and coding jobs for a mock executor. Genkit receives bounded context but does not own a
session store, another transcript, or coding-job persistence.

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
| `runs` | S11 logical workflow envelopes. A submitted mock job leaves its run in `waiting_for_jobs`; S12 owns result consumption and run continuation. |
| `coding_jobs` | S11 independently claimable coding work with immutable pinned input, renewable leases, fenced attempts, and one structured terminal result. |
| `schema_migrations` | Applied runtime-database schema versions. |
| `identities`, `slack_threads`, `event_receipts`, `automations`, `automation_runs`, `code_reviews`, `review_projections` | Planned collections created only by the later slice that first writes them. |

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

### `runs` fields

S11 deliberately gives the run no lease and no copied job result. It is the durable workflow
envelope that S12 will resume after observing a terminal coding job.

| Field | Purpose | Example |
|---|---|---|
| `_id` | Stable logical run ID. Multiple coding jobs may belong to one run. | `run-42` |
| `schema_version` | Strict document-shape version. | `"1"` |
| `project` | Project/domain boundary shared by every child job. | `platform` |
| `conversation_id` | Durable conversation to resume after restart. | `conversation-42` |
| `source_turn_id` | Exact conversation turn that requested this workflow. | `turn-7` |
| `status` | S11 persists `waiting_for_jobs`; later workflow slices add consumer-owned transitions. | `waiting_for_jobs` |
| `created_at`, `updated_at` | Creation and last workflow-envelope mutation times. Job progress does not rewrite the run in S11. | `2026-10-05T16:00:00Z` |

### `coding_jobs` fields

| Field | Purpose | Example |
|---|---|---|
| `_id` | Stable caller-supplied job ID used for status, cancellation, and external-resource labels. | `job-42` |
| `schema_version` | Strict document-shape version. | `"1"` |
| `run_id` | Parent workflow run. It is non-unique because later workflows may submit several jobs. | `run-42` |
| `project` | Project/domain boundary duplicated for indexed idempotency and diagnostics. | `platform` |
| `submission_idempotency_key` | Caller retry identity. `(project, submission_idempotency_key)` is unique. | `event-7:fix` |
| `request_fingerprint` | SHA-256 of the complete immutable request. A changed retry is rejected. | `sha256:8d0f...` |
| `request` | Strict input: pinned repository revision, objective, content revision, generic skill lock, mode, harness, and deadline. | See sample. |
| `deadline_at` | Indexed BSON date duplicated from the request for atomic claim/renew/complete checks. | `2026-10-05T16:30:00Z` |
| `status` | Lifecycle: `queued`, `running`, `completed`, `failed`, `timed_out`, `cancelled`, or `needs_input`. | `completed` |
| `attempt` | Monotonic fencing generation. Claim/reclaim increments it; renewal does not. | `2` |
| `worker_id` | Bounded diagnostic owner and claim binding. It is not a credential or worker registry. | `executor-3` |
| `lease_expires_at` | Renewable supervision deadline while running; `null` otherwise. | `2026-10-05T16:11:00Z` |
| `cancel_requested_at` | Time cancellation won the terminal-state race, or `null`. | `null` |
| `result` | One strict structured success result, only for `completed`; large artifacts stay external. | See sample. |
| `outcome` | Domain conclusion only for `completed`: `fixed`, `no_fix_found`, or `unsafe_to_proceed`. | `fixed` |
| `result_fingerprint` | Replays the exact same completion and rejects a contradictory one. | `sha256:19ac...` |
| `failure` | Bounded execution/contract failure only for `failed`. | `null` |
| `input_request` | Bounded question/checkpoint only for `needs_input`; S12 owns continuation. | `null` |
| `created_at`, `updated_at` | Creation and latest state-mutation times. | `2026-10-05T16:00:00Z` |
| `started_at` | Start of the current fenced attempt; it changes on reclaim. | `2026-10-05T16:10:00Z` |
| `completed_at` | Terminal persistence time, or `null` while queued/running. | `2026-10-05T16:12:00Z` |

Abbreviated completed records:

```json
{
  "run": {
    "_id": "run-42", "schema_version": "1", "project": "platform",
    "conversation_id": "conversation-42", "source_turn_id": "turn-7",
    "status": "waiting_for_jobs", "created_at": "2026-10-05T16:00:00Z",
    "updated_at": "2026-10-05T16:00:00Z"
  },
  "coding_job": {
    "_id": "job-42", "schema_version": "1", "run_id": "run-42", "project": "platform",
    "submission_idempotency_key": "event-7:fix", "status": "completed",
    "request_fingerprint": "sha256:8d0f...",
    "request": {
      "schema_version": "1", "job_id": "job-42", "run_id": "run-42",
      "conversation_id": "conversation-42", "source_turn_id": "turn-7",
      "submission_idempotency_key": "event-7:fix", "project": "platform",
      "repository": "company/service", "repository_revision": "commit-123",
      "objective": "Repair the worker", "mode": "fix", "harness": "mock",
      "content_revision": "content-abc",
      "selected_skill_lock": {"schema_version": "1", "target": "generic", "catalog_revision": "content-abc", "project": "platform", "packages": []},
      "deadline_at": "2026-10-05T16:30:00Z"
    },
    "deadline_at": "2026-10-05T16:30:00Z", "attempt": 1,
    "worker_id": null, "lease_expires_at": null, "cancel_requested_at": null,
    "result": {"schema_version": "1", "job_id": "job-42", "outcome": "fixed", "summary": "Worker repaired", "changed_paths": ["src/worker.py"], "checks": ["pytest"]},
    "outcome": "fixed", "result_fingerprint": "sha256:19ac...",
    "failure": null, "input_request": null,
    "created_at": "2026-10-05T16:00:00Z", "updated_at": "2026-10-05T16:12:00Z",
    "started_at": "2026-10-05T16:10:00Z", "completed_at": "2026-10-05T16:12:00Z"
  }
}
```

Submission creates an absent run and its job in one transaction. An exact retry returns the existing
snapshot. Claim is an atomic queue update. The supervisor renews while the harness runs and commits
terminal state only while `status`, `worker_id`, `attempt`, lease, and deadline still match.
Cancellation, timeout, and completion are therefore a one-winner compare-and-set race. Recovery
requeues expired running attempts; the next claim increments `attempt`. The durable terminal job is
the portable v1 completion signal, so S11 has no outbox or notification collection.

The disposable mock harness never receives Mongo credentials or declares authoritative completion.
It returns a structured object; the persistent executor validates and stores it. S13 adds external
process/container observation, artifact collection, and supervisor checks behind the same boundary.

### Runtime schema changes

Startup migration creates missing collections, applies strict validators with `collMod`, creates the
required indexes idempotently, and records `runtime-schema-v1` in `schema_migrations`. The
`schema_version` inside each document identifies that document's validated shape; the migration
record identifies which database-level setup has run. A later incompatible shape requires an
explicit v2 migration and data transition before changing the validator—it is not inferred from the
Pydantic model and old records are never silently reinterpreted.

S11 additionally records `runtime-jobs-schema-v1`. The distinct marker prevents the existing S10
runtime migration from falsely implying that job validators and indexes exist.

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

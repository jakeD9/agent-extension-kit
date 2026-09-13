# MongoDB Data Model

## Overview

The default deployment uses one MongoDB database named `agent_extension_kit`. Separate collections preserve ownership, retention, and access boundaries without requiring multiple databases.

Only `knowledge_chunks` is implemented today. The remaining collections are reserved by the architecture and will be introduced by their roadmap slices. Applications should use repository interfaces rather than accessing collections directly.

## Context collections

| Collection | Purpose | Primary owner | Lifecycle |
|---|---|---|---|
| `knowledge_documents` | Metadata for each Git-owned source document, including project, authority, owner, and current revision. | Context service | Replaced when a source is re-indexed; Git remains canonical. |
| `knowledge_chunks` | Searchable sections derived from approved documents, with access groups and revision-pinned citations. | Context service | Rebuilt during ingestion; stale chunks are removed. |
| `knowledge_source_revisions` | Ingestion attempts, pinned Git revisions, counts, and failures. | Context service | Append-only operational history with configurable retention. |
| `skills` | Searchable metadata for validated Git-owned skill packages. | Context service | Rebuilt during ingestion; skill files remain canonical in Git. |
| `memories` | Approved team memories that may appear in retrieval results. | Context service | Retained until expiration or supersession; never silently promoted. |
| `memory_proposals` | Candidate memories, evidence, review state, and approval history. | Context service | Retained for audit according to team policy. |
| `context_audit_events` | Knowledge, memory, skill, and tool decisions with principal, policy, and outcome metadata. | Context service | Append-only with a defined retention period. |

## Runtime collections

| Collection | Purpose | Primary owner | Lifecycle |
|---|---|---|---|
| `slack_threads` | Mapping between Slack threads and durable conversations. | Slack agent | Retained while conversation continuity is required. |
| `conversations` | Model messages, tool results, and summarized thread context. | Slack agent | Subject to conversation and privacy retention policy. |
| `agent_runs` | Status, timing, limits, and results for user, automation, and coding runs. | Slack worker | Retained for operations and audit. |
| `approvals` | Pending and resolved approval requests for consequential actions. | Slack worker | Retained with the related run for audit. |
| `event_receipts` | Deduplication records for signed Slack and other incoming events. | Slack agent | Expires after the provider retry window plus a safety margin. |
| `automations` | Enabled triggers, conditions, schedules, limits, cursors, and authorization policy. | Scheduler | Retained until explicitly removed; disabled definitions remain inspectable. |
| `automation_runs` | Scheduled occurrences, leases, attempts, counts, and terminal failures. | Scheduler and worker | Retained for recovery and operational review. |
| `code_reviews` | Review identity, exact head revision, publication result, and projected Slack reaction state. | Slack worker | Retained long enough to enforce idempotency and explain outcomes. |

## Access rules

- The context service reads and writes context collections only.
- The Slack agent, scheduler, and worker read and write runtime collections only.
- The coding runner and local agents receive no MongoDB credentials.
- Production deployments should use separate application identities with collection-scoped grants.
- Cross-area workflows communicate through service contracts, not direct cross-owner collection reads.

## Shared document requirements

Every durable record should include a stable identifier and creation timestamp. Records tied to work should also carry the project and run identifier. Retriable operations store their idempotency key, while leased work stores lease owner and expiry. Sensitive payloads must be minimized or redacted before storage.

Collection validators and indexes are added with the slice that first writes each collection. Their tests must cover uniqueness, authorization filters, retention fields, and the retry or lease behavior relevant to that collection.

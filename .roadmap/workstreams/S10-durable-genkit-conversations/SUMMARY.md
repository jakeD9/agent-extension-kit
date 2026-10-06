# S10 Summary — Durable Genkit Conversations

## Delivered

- Extended the provider-neutral turn request with optional, strictly bounded conversation context:
  a deterministic summary plus at most six recent completed exchanges and a 48,000-character
  aggregate limit. The stateless S09 JSON probe continues to omit it.
- Added an application-owned durable conversation wrapper around `AgentRuntime`. One v1 worker uses
  a process-local lock per conversation while different conversations can execute concurrently.
- Added strict MongoDB validators, migrations, and indexes for `conversations` and
  `conversation_turns`. Claim acquisition and terminal completion are multi-document transactions;
  monotonic claim generations reject stale completions after expiry.
- Persisted each original request and terminal result. Successful results include citations,
  decisions, usage, exact Git content revision, and immutable selected skill lock; early failures
  retain bounded failure evidence without inventing unavailable provenance. Request fingerprints
  provide idempotent replay and reject reuse of a run ID with different input.
- Added deterministic compaction with a 12,000-character summary, 36,000-character recent-turn
  budget, bounded seven-document Mongo reads, and self-healing compaction before context assembly.
- Kept history outside Genkit. The coordinator receives summary/history as user/model conversation
  data, preserves the current objective as the final user message, and does not configure a
  `SessionStore` or snapshot transcript.
- Added bounded failures for runtime exceptions, cancellation, mismatched result identity, expired
  claims, and oversized turn documents. Turn payloads are capped at 8 MiB before Mongo persistence.

## Verification evidence

- The deterministic suite passed 233 tests with environment-gated integrations skipped.
- Focused runtime/coordinator coverage passed 27 tests after independent review fixes.
- Ruff lint/format, strict mypy, lockfile consistency, and diff checks passed.
- A live MongoDB 8.0 replica-set integration used the `agent_runtime` service credential and passed
  transactional claim/completion, process restart, bounded continuation, and idempotent replay.
- Independent review first identified and drove fixes for the claim-insert crash window, ordinary
  cancellation persistence, an incomplete bounded-query index, result identity mismatch, and
  non-atomic completion. The clean re-review found no remaining correctness issue.

## S11 inputs and boundaries

- S11 may reuse the explicit transaction/idempotency patterns, but conversation claims remain
  distinct from coding-job worker claims.
- Keep job lifecycle separate from domain outcome: a completed job may report `fixed`,
  `no_fix_found`, or `unsafe_to_proceed`.
- Add only the minimal `runs` and `coding_jobs` behavior needed for the mock vertical slice. Do not
  introduce generalized workflow-step, action-intent, or outbox frameworks without a demonstrated
  failure boundary.
- S09 remains the stateless diagnostic/CI probe; local Codex/Claude skills, MCP, and context CLI
  remain the primary local developer workflow.

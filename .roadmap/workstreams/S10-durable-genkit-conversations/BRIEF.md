# S10 — Durable Genkit Conversations

Depends on S09. Add one application-owned conversation history made of ordered turns and a bounded
summary. Persist each user request and terminal result. Every successfully completed turn includes
citations, usage, its exact Git content revision, and immutable skill lock; failed turns preserve
their bounded failure evidence and may fail before a revision or lock is available. A later turn may
use newer canonical knowledge and skills; the conversation does not freeze its first revision. Jobs
created from a turn pin an exact revision for their lifetime.

V1 runs one active coordinator-worker process. Process different conversations concurrently and
serialize the same conversation with a process-local lock. Persist only the crash/restart claim
fields `active_turn_id`, `claim_generation`, and `claim_expires_at`; use compare-and-set completion
to reject stale generations. Do not add a renewable lease loop, worker-owner registry, or opaque
lease token.

Invoke the S09 runtime with an optional bounded `conversation_context` containing the deterministic
summary and only the recent uncompacted completed turns. Never retrieve or replay the full
transcript. The S09 probe omits the context and remains stateless. Atomically append each result and
compact at fixed limits. Maintain one authoritative history; do not use Genkit `SessionStore`,
snapshot ancestry, branching, or another framework-owned transcript. Slack thread mapping belongs
to S14. Exclude durable coding jobs.

Verify ordered continuation, exact per-turn provenance, later-revision adoption, deterministic
compaction bounds, restart continuation, same-conversation serialization, different-conversation
concurrency, expired-claim recovery, request-id replay/conflict behavior, failed-turn persistence,
and stale-generation rejection.

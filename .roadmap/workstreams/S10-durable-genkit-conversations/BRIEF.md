# S10 — Durable Genkit Conversations

Depends on S09. Add one application-owned conversation history made of ordered turns and a bounded
summary. Persist each user request, assistant result, citations, usage, exact Git content revision,
and immutable skill lock used by that turn. A later turn may use newer canonical knowledge and
skills; the conversation does not freeze its first revision. Jobs created from a turn pin an exact
revision for their lifetime.

Use compare-and-set revisions plus leases/fencing for same-conversation serialization and stale
worker rejection. Invoke the S09 runtime with the bounded summary and recent turns, then atomically
append the result and compact when limits are reached. Evolve the provider-neutral request with an
optional bounded `conversation_context`; the S09 probe omits it and remains stateless. Maintain one
authoritative history; do not build snapshot ancestry, branching, or a second framework-owned
transcript. Slack thread mapping belongs to S14. Exclude durable coding jobs. Verify ordered
continuation, exact per-turn provenance, later-revision adoption, compaction bounds, restart
continuation, concurrent claims, lease expiry, failed-turn handling, and stale-worker rejection.

# S08 — Durable Genkit Conversations

Depends on S07. Implement `MongoSessionStore` against the exact pinned Genkit contract, or the
documented application-history fallback selected in S05. Map application conversations to sessions,
snapshots, parents, bounded chunks, and latest pointers; add Slack-thread identity mapping, leases,
compare-and-set revisions, fencing, and same-conversation serialization. Maintain one authoritative
history. Exclude durable coding jobs. Verify session/snapshot retrieval, branching ambiguity,
atomic updates, compaction bounds, restart continuation, abort semantics actually used, concurrent
claims, lease expiry, and stale-worker rejection.

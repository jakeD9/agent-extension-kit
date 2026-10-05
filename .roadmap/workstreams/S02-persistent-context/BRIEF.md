# S02 — Persistent Context Boundary

> Historical slice brief. MongoDB remains the persistence adapter, but its Git knowledge records are
> rebuildable projections for one trusted domain, not independently authoritative content.

Depends on S01. Replace the in-memory production adapter with `team_context` MongoDB repositories,
explicit migrations, validators, indexes, revision synchronization, and readiness checks. Configure
separate `team_context` and `agent_runtime` databases and credentials; the context service must have
no runtime-database access. Acceptance: cited search survives restart and denied scope never becomes
a retrieval candidate. Exclude Genkit sessions and all runtime records. Verify repository, migration,
authorization, restart, image, and Compose tests.

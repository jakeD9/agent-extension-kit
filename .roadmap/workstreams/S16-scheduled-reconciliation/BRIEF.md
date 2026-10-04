# S16 — Scheduled Reconciliation

Depends on S15. Add scheduler mode, unique occurrences, renewable leases, bounded channel scans,
cursors, durable retry ledger, backoff, concurrency/cost caps, and review/reaction reconciliation.
Cursor advancement must not lose failed or deferred targets. Verify worker crash and lease expiry,
repeated occurrences, missed events, new heads, partial scan failure, exhausted work, and reaction
repair without repeated review.

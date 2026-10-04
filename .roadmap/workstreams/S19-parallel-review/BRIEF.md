# S19 — Bounded Parallel Review

Depends on S16. Use explicit Python workflow code and Genkit flows or specialist tools verified in
S07 to run fixed correctness and security branches in separate read-only jobs. Cap roles, depth,
turns, spend, duration, and concurrency. Persist branch completion and the join decision.
Deterministic reconciliation preserves evidence and attribution while handling duplicates,
conflicts, unsupported findings, required-reviewer failure, and stale heads. Retain sequential mode
and verify restart reconciliation without repeated side effects.

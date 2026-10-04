# S11 — Durable Mock Coding Jobs

Depends on S10. Add versioned run, step, coding-job, completion-outbox, action-intent, and artifact
contracts plus Mongo validators/indexes, leased workers, idempotent submission, status/result,
cancellation, deadlines, attempt records, and reconciliation. Implement `MockCodingHarness` and a
mock executor without repository access or publication. Verify crash windows before and after
submission, duplicate notifications, terminal states, structured-result rejection, cancellation,
timeout, restart recovery, and once-visible completion.

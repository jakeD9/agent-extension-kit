# S11 — Durable Mock Coding Jobs

Depends on S10. Add only the durable `runs` and `coding_jobs` records needed for one mock coding-job
path, with versioned contracts, Mongo validators/indexes, atomic claim and lease renewal,
idempotent submission, status/result, cancellation, deadline handling, and restart recovery. A run
records the conversation and source turn that a later consumer must resume. A job pins the exact Git
revision and skill lock selected when it is created. Implement `MockCodingHarness` and a mock executor
without repository access or publication.

Keep lifecycle separate from domain outcome. Lifecycle is `completed`, `failed`, `timed_out`,
`cancelled`, or `needs_input`; a completed result may separately report `fixed`, `no_fix_found`, or
`unsafe_to_proceed`.

Do not introduce generalized workflow-step, action-intent, or completion-outbox frameworks unless a
demonstrated crash boundary requires one. Prefer explicit transitions on the two records and
reconcile ambiguous submission or completion before retrying. Verify concurrent claim, crash before
and after submission, duplicate completion, terminal states, structured-result rejection,
cancellation, timeout, lease expiry, restart recovery, and one visible completion.

S11 workers claim only `coding_jobs`; there is no run worker yet. The terminal job record is the
portable polled completion signal. `attempt` is the sole fencing generation, while bounded
`worker_id` is diagnostic claim binding rather than a secret lease token or worker registry.

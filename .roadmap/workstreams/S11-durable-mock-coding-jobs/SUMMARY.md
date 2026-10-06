# S11 Summary — Durable Mock Coding Jobs

## Delivered

- Added strict provider-neutral snake_case contracts for immutable coding-job requests, run/job
  records, fenced claims, lifecycle status, separate domain outcomes, bounded failures,
  needs-input questions, and structured results. Genkit types remain outside `team_agent_runtime`.
- Added minimal `runs` envelopes that retain the durable conversation and source-turn correlation
  needed for S12 resume. Runs remain `waiting_for_jobs` in S11 and carry no worker claim or copied
  result; multiple jobs may belong to one run.
- Added idempotent job submission keyed by unique `(project, submission_idempotency_key)` with a
  complete request fingerprint. An absent run and its first job are created in one Mongo transaction;
  exact retries replay the authoritative persisted snapshot and changed retries conflict.
- Added strict `runs` and `coding_jobs` MongoDB validators, conditional lifecycle/result validation,
  bounded indexes, and the distinct `runtime-jobs-schema-v1` marker. Job documents remain below an
  8 MiB application limit and retain exact repository, content revision, and generic skill lock
  inputs.
- Added atomic job claims and renewable leases. Each claim/reclaim increments `attempt`, renewal does
  not, and renewal/completion compare status, worker identity, attempt, live lease, and deadline.
  `attempt` is the sole fencing generation; `worker_id` is bounded diagnostic claim binding, not a
  credential, opaque token, or worker registry.
- Added bounded reconciliation for expired leases, cancellation, and deadlines. Expired work is
  requeued for a new attempt, stale workers cannot complete, and cancellation/timeout/completion form
  a one-winner terminal compare-and-set race.
- Added `MockCodingHarness` and a supervisor-owned `MockCodingExecutor`. The executor claims and
  renews work, validates structured harness output, persists bounded failure for malformed output,
  and leaves an interrupted job leased for restart recovery. The mock harness has no repository,
  MongoDB, publication, Docker, Kubernetes, or Git credentials.
- Made the durable terminal `coding_jobs` record the portable polled completion signal. S11 adds no
  run-worker claim, workflow-step/action framework, event bus, notification collection, or outbox.
- Updated the canonical architecture and annotated data-model documentation with ownership,
  lifecycle, schema fields, samples, completion flow, and S11/S12 boundaries.

## Verification evidence

- The final deterministic suite passed 245 tests with five explicitly environment-gated
  integrations skipped.
- Focused S11 coverage passed 12 deterministic tests when the opt-in live MongoDB test was omitted.
- A live MongoDB replica-set run passed both the S11 coding-job integration and S10 conversation
  regression tests (2/2) using the `agent_runtime` service credential. It exercised transactional
  submission/replay, changed-input conflict, concurrent claim, lease renewal, restart, duplicate
  completion, expired reclaim and stale fencing, cancellation, timeout, and direct validator
  rejection.
- Ruff lint and formatting passed across 51 files; strict mypy passed across 26 source files. Lockfile
  consistency, builds for all nine workspace distributions, Compose validation, and `git diff
  --check` passed.
- Independent review drove fixes for cross-run job identity, conversation/source-turn recovery,
  attempt-sensitive replay, deadline/lease fencing, executor heartbeat timing, interrupted-worker
  recovery, wrong-result identity, path safety, full-document size checks, and BSON timestamp
  normalization. The final re-review found no remaining correctness issue.

## S12 inputs and boundaries

- S12 owns consumption of one terminal coding job, coordinator resume, and run-state transitions.
  It should poll the durable job record and deduplicate consumption by job, attempt, and result
  fingerprint without introducing an outbox unless a demonstrated delivery boundary requires one.
- Use `runs.conversation_id` and `runs.source_turn_id` to enter the existing per-conversation
  serialization path after restart. Do not infer the conversation from a globally ambiguous turn
  identifier.
- Preserve lifecycle/outcome separation. `completed` may carry `fixed`, `no_fix_found`, or
  `unsafe_to_proceed`; `needs_input` carries a bounded question/checkpoint and is not a successful
  domain outcome.
- The S12 mock workflow may submit and consume jobs but still excludes repository access, Docker,
  Kubernetes, Codex/Claude harness execution, Git publication credentials, Slack, and a generalized
  workflow-step framework.
- S13 owns disposable execution environments, external process/container observation, artifact
  collection, supervisor-owned diff/test validation, and real harness termination/reconciliation.

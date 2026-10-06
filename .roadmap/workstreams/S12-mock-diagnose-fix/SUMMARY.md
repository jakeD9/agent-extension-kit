# S12 Summary — Mock Diagnose-and-Fix Workflow

## Delivered

- Added a thin application-owned workflow that executes one durable cited `diagnose-and-fix`
  planning turn, derives deterministic job/submission identities, and transactionally submits one
  mock job pinned to its repository revision, canonical content revision, skill lock, and planning
  citations.
- Added bounded polling for terminal unconsumed jobs and a short, non-renewed, generation-fenced run
  claim. Run consumption has no worker owner, opaque token, heartbeat, outbox, workflow-step table,
  or generalized scheduler. Multi-job runs are skipped because joins remain outside S12.
- Added a deterministic completion fingerprint for every terminal lifecycle, including failures,
  cancellation, timeout, and `needs_input`, plus a deterministic resume-turn ID. Retried model and
  memory operations replay under those identities, providing effectively-once visible behavior.
- Added typed bounded job-completion context to coordinator requests. Genkit renders it as labelled
  untrusted application data, resolves current canonical knowledge and skills for the resumed turn,
  and retains the coding job's pinned provenance for reporting.
- Added deterministic mock draft-PR outcomes only for `completed/fixed`. No Git provider or external
  publication operation is performed.
- Added optional reusable-lesson suggestions and an idempotent context-service adapter that writes
  supplemental memory with observed canonical provenance, planning evidence, and a deterministic
  expiry/key. Stale lesson suggestions are skipped rather than blocking workflow completion.
- The S12 resume path may explicitly retry a retriable failed conversation turn under the same
  deterministic turn ID and a fresh sequence; default S10 behavior, completed turns, and
  non-retriable failures still replay exactly. This prevents a transient provider failure from
  permanently poisoning completion consumption without changing other callers.
- Added one final transaction that marks the job consumed and the run terminal together. A crash
  before it leaves the terminal job discoverable; stale claim generations cannot finalize it.
- Explicitly migrated `runs` and `coding_jobs` from strict schema v1 to v2 by temporarily relaxing
  their validators, backfilling new fields, and reinstalling strict validators and the bounded
  terminal-unconsumed index.
- Kept the stateless S09 CLI probe narrow by rejecting both durable conversation context and S12
  completion context.

## Verification evidence

- The deterministic workspace suite passed 264 tests with five environment-gated integrations
  skipped.
- Focused S12 tests cover cited start/submission, every terminal mapping, optional memory, a crash
  after conversation resume but before final consumption, retriable turn recovery, deterministic
  replay, mismatch rejection, and one visible consumed result.
- Coordinator coverage proves completion context is labelled as untrusted application data; CLI
  coverage proves that context cannot enter through the stateless probe.
- Ruff and strict mypy passed for the changed runtime packages.
- The opt-in live Mongo integration was extended to exercise v2 run claiming and transactional job
  consumption. It remains environment-gated when `AGENT_RUNTIME_MONGODB_URI` is unavailable.

## S13 inputs and boundaries

- S13 replaces the production mock execution boundary with supervised disposable Codex execution;
  the deterministic mock remains test support until the repository-cleanup slice relocates it.
- The disposable runner still never writes Mongo directly. A persistent executor observes process
  termination, validates bounded output and artifacts, and commits the authoritative terminal job.
- Preserve S12 completion identity and consumption semantics while adding container reconciliation.
  Do not give runner containers Mongo or publication credentials.

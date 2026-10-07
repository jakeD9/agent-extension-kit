# S13 Summary — Supervised Codex Runner

## Delivered

- Added provider-neutral execution policy, phase-progress, and durable artifact contracts. Jobs now
  use explicit request/result schema v2 and coding-job document schema v3.
- Added a restart-safe, idempotent v1/v2-to-v3 Mongo migration that atomically rewrites each job's
  nested request/result shapes while preserving S11/S12 request, result, and completion identities
  for legacy mock jobs.
- Added `LocalRepositoryCatalog`, which admits only configured local Git sources, requires a full
  commit object ID, and emits a bundle from an isolated bare staging clone.
- Added frozen Codex-target skill staging from the job's exact generic lock. The disposable runner
  receives only the verified projection and cannot select a newer package.
- Added a narrow argv-only Docker adapter and `LocalDockerExecutor` with renewable Mongo attempt
  fencing, harness-scoped claims, deterministic container labels, same-worker restart recovery,
  bounded phases/artifacts, and cleanup after durable completion. The persistent worker is exposed
  as `team-agent-coding-executor`, with a one-cycle probe mode and explicit repository allowlist.
- Added a non-root `coding-runner` image target pinned to Codex CLI 0.154.0-alpha.6.2. The harness
  uses ephemeral strict config, workspace-write sandboxing, automatic review, JSONL progress, and a
  strict output schema.
- Added verifier-derived patch/path validation (including untracked files), patch/output/resource
  limits, a simple editable path-prefix boundary, and a separately named credential-free,
  network-off verifier container. Runner output is read-only to the verifier; the supervisor copies
  validated outputs into collision-resistant, supervisor-owned durable artifact roots.
- Added restart reconciliation for runner and verifier containers, deterministic verifier reruns,
  terminal-orphan cleanup across both managed container kinds, ambiguity-safe Docker create, strict
  attempt fencing, and explicit timed-out lifecycle transitions.
- Kept S12 intact: Mongo owns lifecycle, the runner has no Mongo/context/publication credentials,
  and mock publication/conversation resumption still happen after terminal job consumption.

## Verification evidence

- The deterministic runner test uses a real exact-revision Git bundle and fake Codex executable
  with the Docker network policy fixed to `none`. An opt-in real-Docker test executes the same repair
  as the non-root runtime user and verifies mounts, skill discovery, patching, verification, and
  durable artifacts end to end.
- Tests cover structured-output bounds, strict JSONL parsing, local-source admission, full-SHA
  enforcement, deterministic labels, non-root tmpfs options, verifier isolation, artifacts before
  completion, cleanup, and out-of-bound changed paths becoming `unsafe_to_proceed`.
- Migration coverage rewrites legacy nested request/result payloads and preserves request/result
  fingerprints; the opt-in Mongo integration repeats that regression against a real collection and
  confirms strict validation is restored.
- The default suite remains credential-free. Separately gated image tests verify both the pinned
  Codex executable and the fake-Codex Docker repair path when a built image is supplied. Real model
  execution remains explicitly credential-gated and outside ordinary CI.

## S14 inputs and boundaries

- S14 can consume the existing cited coordinator and durable workflow surface; it should not call
  the coding container directly from Slack ingress.
- Preserve one team/domain admission boundary, application-owned conversation history, and Git as
  canonical knowledge/skill authority.
- Real publication, broad credentials, remote Git provider cloning, and a production Codex
  credential broker remain outside S13.

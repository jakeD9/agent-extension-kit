# S05 Summary — Verified Skill Pull and Lockfile CLI

## Delivered

- Added the shared `team-agent-skills` Python package with an authenticated, bounded `SkillClient`
  and provider-neutral `SkillInstaller`.
- Added `team-agent skills list` and `team-agent skills pull` with named, all, and exact-lock
  selection; optional current-revision requirements; generic targets; JSON output; and environment-
  only credentials.
- Added schema-v1 snake_case `skills.lock.json`, a verified content-addressed cache, and a separate
  ownership manifest recording every installed path, size, and hash.
- Independently validates canonical package IDs, manifest/file metadata, safe noncolliding paths,
  resources, strict base64, sizes, and SHA-256 hashes without executing package scripts.
- Preserves unmanaged and unselected content, refuses edits or unexpected files inside managed
  packages, and commits packages plus metadata through a recoverable, idempotent transaction journal.
- Frozen pulls never resolve latest: they use the exact lock with a verified cache or exact service
  package and fail when neither can satisfy it.

## Verification evidence

- The deterministic suite passed 93 tests; the opt-in live-Mongo integration test was skipped
  because no test MongoDB URI was configured.
- The 34 S05 tests cover named/all/empty/frozen pulls, lock-derived projects, cache corruption,
  canonical identity and byte verification, path/resource/symlink attacks, user-file preservation,
  reserved metadata collisions, bounded/cyclic listing, token nondisclosure, transaction rollback,
  and recovery interrupted during rollback itself.
- Ruff lint/format, configured strict mypy, direct mypy over the new source and tests, lockfile
  consistency, CLI entrypoint smoke checks, diff checks, and builds for all six Python distributions
  passed immediately before the S05 commit.
- No Codex/Claude layout adapter, MCP server, Genkit runtime, or coding job was added or claimed.

## S06 inputs and constraints

- Reuse the same Python contracts and application services for JSON-first context CLI and stdio MCP;
  do not fork authorization, error, or snake_case behavior.
- Add only project-scoped Codex and Claude layout adapters, preserving the original package lock and
  provenance; never install globally by default.
- Prove actual pinned harness discovery behavior with fixtures or opt-in live checks instead of
  assuming a directory layout is recognized.
- Keep governed memory in S08 and Genkit compatibility/runtime work in S07/S09.

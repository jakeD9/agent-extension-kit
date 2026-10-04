# S13 — Supervised Codex Runner

Depends on S12. Implement `LocalDockerExecutor` and the real pinned noninteractive Codex CLI harness
inside the coding-runner image. Resolve selected skills when the job is created, persist its immutable
lock as job input, and stage verified packages during bootstrap for the job lifetime before the
frozen Codex-target installation; a verified cache may satisfy the lock, while missing exact packages
block startup without fallback. Executor staging supports network-restricted runners. Use exact
repository revisions, ephemeral workspaces, bounded structured output, capability policy,
resource/time limits, controlled caches, diff limits, and independent checks without publication
credentials. Publication remains mocked.
Verify the fixture repair, lock reuse, install-failure startup blocking, actual Codex discovery when
available, restart/container reconciliation, unsafe commands/diffs, timeout/cancel, and cleanup.

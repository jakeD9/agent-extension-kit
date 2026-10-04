# S11 — Supervised Codex Runner

Depends on S10. Implement `LocalDockerExecutor` and the real pinned noninteractive Codex CLI harness
inside the coding-runner image. Use exact repository revisions, ephemeral workspaces, bounded
structured output, capability policy, resource/time limits, controlled caches, diff limits, and
independent checks in an environment without publication credentials. Publication remains mocked.
Verify the fixture regression repair, restart/container-label reconciliation, forbidden paths and
commands, oversized diffs, false success claims, timeout/cancel, cleanup, and credential absence.

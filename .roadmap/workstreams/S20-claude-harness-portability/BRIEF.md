# S20 — Claude Harness Portability Proof

Depends on S11. Add `ClaudeHarness` through the pinned official Python Claude Agent SDK behind the
existing `CodingHarness` contract. Run it inside the same disposable executor boundary with explicit
project settings, permissions, MCP configuration, progress/result normalization, cancellation,
session artifact versioning, and independent supervisor checks. Do not make Claude a coordinator
provider in this slice. Verify the fixture workflow with deterministic fixtures and an opt-in bounded
live harness run without core workflow changes.

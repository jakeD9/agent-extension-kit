# S06 Summary — Local MCP and Harness Parity

## Delivered

- Added the `team-agent-context` Python package with a bounded authenticated async client, a
  snake_case JSON CLI, and a stdio MCP server over the existing context-service contracts.
- Exposed three read-only MCP tools for cited knowledge search and authorized skill list/get, with
  typed schemas, bounded inputs, opaque pagination, safe errors, and managed client lifecycle.
- Added deterministic REST/CLI/MCP parity coverage, including indistinguishable hidden and missing
  skills, plus a real stdio negotiation test and ten multi-call MCP evaluation specifications.
- Added project-scoped Codex and Claude skill installation targets using `.agents/skills` and
  `.claude/skills`, preserving immutable locks, package provenance, ownership, and atomic recovery.
- Enforced canonical project destinations and caches for harness targets, rejected unsafe service
  URLs and cache/destination symlink topology, and kept the generic target available for explicit
  provider-neutral destinations.
- Added checked-in project MCP launch examples, harness discovery fixtures, opt-in live discovery
  tests, and local adapter/security documentation.

## Verification evidence

- The deterministic suite passed 156 tests; three opt-in tests were skipped when live MongoDB or
  harness prerequisites were unavailable. The final skill-distribution subset passed 64 tests.
- Ruff lint/format, configured strict mypy, lockfile consistency, builds for all seven Python
  distributions, `git diff --check`, real stdio MCP negotiation, and strict MCP Inspector
  `tools/list` validation passed during the slice.
- Inspector returned exactly the three typed tools, including the bounded skill-name and revision
  schemas. No live model executed `evals/context-mcp.xml`; the file is a checked structural eval set.
- Local Codex was recorded as `codex-cli 0.154.0-alpha.6.2`. Claude was unavailable locally, so its
  project layout is documented from the official fixture and its live test remains opt-in.

## Boundary and S07 inputs

- Project-scoped installer checks are defense in depth against wrong paths and unsafe existing
  topology; the isolated runner/container workspace is the security boundary against another
  process with equal filesystem privileges.
- The context CLI and MCP remain usable without Genkit. S07 may consume their provider-neutral
  contracts but must not move authorization or durable execution into model code.
- S07 should pin and validate the Genkit Python/OpenAI compatibility matrix; governed memory remains
  S08 and coordinator behavior remains S09.

# S01 — Python Cited-Search Tracer

## Goal and dependencies

Deliver the smallest Python vertical slice: validate the Git-owned extension pack and return
authorized, revision-pinned knowledge through REST. Dependencies: none.

## Contracts and boundaries

Use Pydantic contracts, an authentication port, a provider-neutral knowledge-index port, and a
FastAPI transport. Git content remains canonical. Retrieval must filter project, group, and approved
authority before scoring. This slice is in-memory only.

## Exclusions

MongoDB, skill endpoints, memory, CLI/MCP, ADK, Slack, coding jobs, and removal of the TypeScript
parity implementation.

## Acceptance and verification

- Authorized fixture search returns the expected repository, path, revision, and heading.
- Inaccessible content is absent before scoring; invalid authentication and input use error envelopes.
- Health/readiness report the loaded content pack; `team-context` runs non-root in its image.
- Run lock, Ruff, formatting, mypy, pytest, workspace build, and image checks.


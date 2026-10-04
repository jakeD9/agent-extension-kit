# Agent Extension Kit

A forkable reference implementation for giving team agents approved knowledge, reusable skills,
governed memory, and narrowly scoped tools. Genkit Python will coordinate hosted agents through an
initial OpenAI model path, while the extension kit remains usable by local Codex and Claude clients
without Genkit or database credentials.

The migration is intentionally incremental. The current S03 implementation provides authenticated,
revision-pinned knowledge search and authorized skill discovery/loading from a persistent
MongoDB-backed Python service. CLI/MCP, Genkit orchestration, coding runners, Slack, and automations
arrive in later roadmap slices.

## Quick start

Requirements: Docker with Compose. The development stack creates separate database users for
`team_context` and `agent_runtime` and persists MongoDB data in a named volume.

```sh
cp .env.example .env
docker compose up --build
```

In another terminal:

```sh
curl -s http://localhost:3000/v1/knowledge/search \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"query":"stable idempotency key","project":"event-ingestion"}'

curl -s 'http://localhost:3000/v1/skills/diagnose-and-fix?project=event-ingestion' \
  -H 'Authorization: Bearer dev-token'
```

`/ready` returns 503 until MongoDB is reachable, migrations have applied, and the configured Git
revision has synchronized. To run the process directly, start MongoDB first, load `.env`, and use
`uv run team-context-service`.

## Repository guide

- `apps/` contains independently runnable processes. The Python context service is the first ported app.
- `packages/` contains provider-neutral contracts and application ports.
- `extension/` is the replaceable, Git-owned knowledge and skill pack.
- `docs/architecture.md` records trust boundaries and implementation status.
- `ROADMAP.md` is the dependency-ordered delivery ledger.
- `.roadmap/workstreams/STATUS.md` tells a fresh session what to work on next.
- `.agents/skills/developing-genkit-python/` contains the project-scoped Genkit development skill.

The hyphenated TypeScript application and package directories remain temporarily as a parity oracle.
They are removed in S04 after Python REST, CLI, and MCP parity is verified.

## Development

```sh
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build --all-packages
```

The default suite skips the live MongoDB integration test. With the Compose stack running:

```sh
TEST_TEAM_CONTEXT_MONGODB_URI="${TEAM_CONTEXT_MONGODB_URI:-mongodb://team_context_service:local-context-password@127.0.0.1:27017/team_context?authSource=team_context}" \
  uv run pytest tests/integration/test_persistent_context.py
```

Ordinary CI uses deterministic adapters and fictional fixtures. Live model, coding harness, Slack,
Git, and Atlas checks are opt-in and must never be required by the default test suite.

## Security

This is a reference kit, not a production deployment. The static bearer authenticator is
development-only. See `SECURITY.md` before exposing services or granting repository access.

Licensed under the MIT License.

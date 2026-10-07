# Agent Extension Kit

A forkable reference implementation for giving one trusted domain team canonical Git knowledge,
reusable skills, supplemental shared working memory, and narrowly scoped tools. Genkit Python will coordinate hosted agents through an
initial OpenAI model path, while the extension kit remains usable by local Codex and Claude clients
without Genkit or database credentials.

The migration is intentionally incremental. S01–S08 delivered authenticated, revision-pinned MongoDB
knowledge search, Git-backed skill distribution, local transports, and transactional memory. A
corrective S08 pass replaced their per-artifact groups and memory approval workflow with one
project/domain admission boundary and team-wide supplemental working memory. S09 added the
stateless hosted Genkit coordinator tracer. S10 added application-owned MongoDB conversations with
ordered turns, deterministic bounded context, restart recovery, and exact per-turn provenance.
S11–S13 add durable coding jobs, the diagnose-and-fix workflow, and a supervised disposable Codex
runner. Slack and automations arrive in later roadmap slices.

The protected, pinned Git revision is the sole canonical knowledge and skill source. MongoDB holds a
rebuildable search projection, non-canonical working memory, and application-owned runtime
conversation state. See
[Domain Knowledge Authority](docs/domain-knowledge-authority.md) for this architecture's normative
dogma and [Required CI Implementation](docs/required-ci-implementations.md) for the generic
GitHub/GitLab-compatible publication contract.

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

`/ready` returns 503 until MongoDB is reachable, migrations have applied, and the active knowledge
revision exactly matches the skill catalog's configured Git revision. Production startup is
read-only for publication. The example `.env` explicitly enables local fixture bootstrap and a
mutable development revision; do not carry those two flags into production. Skill discovery is
rebuilt directly from the configured checkout. To run the process directly, start MongoDB first,
load `.env`, and use
`uv run team-context-service`.

## Pulling skills

The `team-agent` CLI reads its context-service URL and bearer token from the environment so secrets
do not enter shell history or lockfiles:

```sh
export TEAM_AGENT_CONTEXT_URL=http://127.0.0.1:3000
export TEAM_AGENT_TOKEN=dev-token

uv run team-agent skills list --project event-ingestion
uv run team-agent skills pull diagnose-and-fix \
  --project event-ingestion --dest .team-agent/skills
uv run team-agent skills pull --lock .team-agent/skills/skills.lock.json \
  --dest /tmp/job-skills --frozen --non-interactive
uv run team-agent skills pull diagnose-and-fix --project event-ingestion \
  --target codex --project-root .
uv run team-agent skills pull diagnose-and-fix --project event-ingestion \
  --target claude --project-root .
```

`skills.lock.json` contains only immutable provenance and hashes. Pulls verify all paths, sizes, and
hashes before replacing any managed package; preserve unmanaged files and unselected packages; and
never execute downloaded scripts. `--json` provides machine-readable list, success, and error output.
The `generic` target writes to `--dest`. Codex and Claude targets require an explicit
`--project-root` and install only to `.agents/skills` or `.claude/skills` below it. Every layout keeps
the same immutable provenance and lock. Lock pulls derive their project from the validated lock; an
optional explicit `--project` must match.

## Querying local context

`team-context` emits the same snake_case contracts as REST by default:

```sh
uv run team-context search "stable idempotency key" --project event-ingestion
uv run team-context skill list --project event-ingestion
uv run team-context skill get diagnose-and-fix --project event-ingestion
uv run team-context memory search "vendor retry key" --project event-ingestion
```

The corrected `team-context-mcp` surface exposes knowledge/skill retrieval plus supplemental memory
search and creation as five stdio tools. It deliberately exposes no memory update, expiry, audit, or
canonical-promotion tool. The checked-in `.codex/config.toml` and `.mcp.json` are project-scoped launch examples;
set `TEAM_AGENT_CONTEXT_URL` and `TEAM_AGENT_TOKEN` in the host environment. See
`docs/local-context.md` for tool behavior, pagination, errors, and verification.

The hosted coordinator has a separate, narrow JSON diagnostic/CI probe. It does not replace the
local Codex/Claude workflow above. See [Hosted Genkit coordinator](docs/coordinator.md) for the
stateless probe, durable conversation wrapper, request contract, evidence rules, and trace procedure.

## Repository guide

- `apps/` contains independently runnable Python processes.
- `packages/` contains provider-neutral contracts and application ports.
- `extension/` is the replaceable, Git-owned knowledge and skill pack.
- `docs/architecture.md` records trust boundaries and implementation status.
- `docs/domain-knowledge-authority.md` defines the normative content-authority dogma.
- `docs/required-ci-implementations.md` defines exact-SHA Git-to-Mongo publication requirements.
- `docs/coordinator.md` documents the hosted Genkit probe and durable conversation wrapper.
- `docs/coding-runner.md` documents pinned Codex execution, isolation, verification, and recovery.
- `ROADMAP.md` is the dependency-ordered delivery ledger.
- `.roadmap/workstreams/STATUS.md` tells a fresh session what to work on next.
- `.agents/skills/developing-genkit-python/` contains the project-scoped Genkit development skill.

The repository is Python-only. Earlier TypeScript applications remain available through Git history
if an implementation comparison is needed.

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
TEST_TEAM_CONTEXT_MONGODB_URI="${TEAM_CONTEXT_MONGODB_URI:-mongodb://team_context_service:local-context-password@127.0.0.1:27017/team_context?authSource=team_context&replicaSet=team-agent-rs&directConnection=true}" \
  uv run pytest tests/integration/test_persistent_context.py
```

Ordinary CI uses deterministic adapters and fictional fixtures. Live model, coding harness, Slack,
Git, and Atlas checks are opt-in and must never be required by the default test suite.

## Security

This is a reference kit, not a production deployment. The static bearer authenticator is
development-only. See `SECURITY.md` before exposing services or granting repository access.

Licensed under the MIT License.

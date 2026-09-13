# Agent Extension Kit

A forkable reference implementation for giving team agents approved knowledge, reusable skills, governed memory, and narrowly scoped tools. The kit keeps canonical content in Git, runtime indexes and state in MongoDB, and model/provider integrations behind replaceable ports.

One MongoDB database is used by default, with separate collections for knowledge, memory, conversations, runs, and automation state.

The first usable release is the local context extension: authenticated REST, CLI, and MCP access to revision-pinned sample knowledge. Slack orchestration, disposable coding jobs, and automation are delivered as later roadmap slices.

## Quick start

Requirements: Node.js 24, pnpm 10, and Docker.

```sh
cp .env.example .env
pnpm install
docker compose up --build
```

In another terminal:

```sh
curl -s http://localhost:3000/v1/knowledge/search \
  -H 'Authorization: Bearer dev-token' \
  -H 'Content-Type: application/json' \
  -d '{"query":"stable idempotency key","project":"event-ingestion"}'

TEAM_CONTEXT_TOKEN=dev-token pnpm team-context search "stable idempotency key" --project event-ingestion
```

The repository-local Codex MCP configuration starts the same CLI in stdio mode. Restart Codex after starting the context service and setting `TEAM_CONTEXT_TOKEN`.

## Customize a fork

1. Change the identity, projects, and access groups in the extension manifest.
2. Replace the clearly fictional Markdown under the knowledge and skills directories.
3. Set a real content revision during deployment; never use `working-tree` for published citations.
4. Replace development bearer identities with an implementation of the authentication port.
5. Run `pnpm check`, `pnpm test`, and `pnpm build` before publishing the fork.

No sample content is imported by framework packages. Removing the sample pack and pointing `CONTENT_PATH` at another validated pack is sufficient.

## Repository guide

- `apps/` contains independently runnable processes and the CLI/MCP bridge.
- `packages/` contains contracts and provider-neutral application ports.
- `extension/` is the replaceable content pack.
- `docs/architecture.md` records trust boundaries and invariants.
- `docs/data-model.md` describes MongoDB collection ownership and lifecycle.
- `ROADMAP.md` is the checkable delivery ledger.

## Development

```sh
pnpm check
pnpm test
pnpm build
```

Tests use deterministic adapters. Live OpenAI, Codex, Slack, GitHub, and Atlas checks are opt-in and must never be required by CI.

## Security

This is a reference kit, not a production deployment. The static bearer authenticator and local Docker executor are development-only. See `SECURITY.md` and the hardening slice before exposing services or granting repository write access.

Licensed under the MIT License.

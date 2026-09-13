# Delivery Roadmap

Checkboxes are updated only after the listed behavior has fresh verification evidence. Each slice is independently demonstrable.

- [x] **S01: Forkable content pack and cited REST search** `risk:high` `depends:[]`
  > Verified: content-pack validation, pre-retrieval authorization, stable citations, MongoDB adapter, REST auth/error behavior, health/readiness, typecheck, and build.
- [x] **S02: Project-scoped CLI and MCP parity** `risk:medium` `depends:[S01]`
  > Verified: REST/CLI/MCP search parity, skill discovery, project-local Codex MCP configuration, typecheck, and build.
- [ ] **S03: Governed team memory** `risk:medium` `depends:[S01,S02]`
  > After this: users propose sourced memories, approvers promote them, and pending, expired, or superseded memories are not authoritative.
- [ ] **S04: Isolated coding runner from the CLI** `risk:high` `depends:[S02]`
  > After this: the idempotency fixture produces a tested patch and mock draft-PR result without exposing Git write credentials.
- [ ] **S05: Grounded Slack Q&A** `risk:high` `depends:[S01,S02]`
  > After this: a verified Slack request receives a cited response, duplicate events are ignored, and thread continuity survives restart.
- [ ] **S06: Slack diagnose-and-fix workflow** `risk:high` `depends:[S03,S04,S05]`
  > After this: the sample error retrieves context and a skill, launches the runner, and posts diagnosis, verification, and a mock PR link.
- [ ] **S07: Event-driven merge-request review** `risk:medium` `depends:[S04,S05]`
  > After this: one Slack merge-request event reviews one exact head SHA once and publishes durable reaction state.
- [ ] **S08: Scheduled reconciliation** `risk:high` `depends:[S07]`
  > After this: a leased six-hour sweep recovers missed events and repairs reaction drift without duplicate reviews.
- [ ] **S09: Security and operational hardening** `risk:high` `depends:[S06,S08]`
  > After this: authorization, approval, injection, limit, recovery, audit, and container-security suites pass.
- [ ] **S10: Provider portability proof** `risk:medium` `depends:[S09]`
  > After this: a second model/harness adapter passes the same contract and workflow suites without core changes.

## Global completion checks

- `pnpm check`
- `pnpm test`
- `pnpm build`
- `docker compose build`
- Slice-specific integration, security, and workflow evals

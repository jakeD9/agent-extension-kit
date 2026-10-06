# Delivery Roadmap

Each slice is designed for one focused implementation session. A checkbox changes only after the
slice's acceptance criteria have fresh verification evidence. The canonical Genkit architecture
brief and the workstream artifacts under `.roadmap/workstreams/` supply fresh-session context.
The domain-authority dogma and required CI publication contract are normative for all slices.

Before any incomplete slice starts, pause for a technical check-in covering its observable outcome,
interfaces and storage, mechanism and data flow, failure/restart behavior, necessary safety
boundaries, explicit exclusions, verification, and unresolved decisions. Implementation begins only
after the team approves that boundary.

## Core v1 path

- [x] **S01: Python cited-search tracer** `risk:high` `depends:[]`
  > After this: an authenticated REST request returns authorized, revision-pinned fixture knowledge from the Python service.
- [x] **S02: Persistent context boundary** `risk:medium` `depends:[S01]`
  > After this: cited retrieval survives restart and the context service cannot access runtime collections.
- [x] **S03: Project skill-catalog tracer** `risk:medium` `depends:[S02]`
  > After the S08 correction: an admitted REST caller can list and load the complete project/domain skill catalog scanned from the pinned Git content pack, without a MongoDB skill catalog.
- [x] **S04: Immutable skill resolution and package API** `risk:high` `depends:[S03]`
  > After the S08 correction: admitted callers can resolve named/all selections to a hashed immutable manifest and download exact self-contained packages from the configured pinned Git revision.
- [x] **S05: Verified skill pull and lockfile CLI** `risk:high` `depends:[S04]`
  > After this: local users and automations can atomically install targeted project packages and reproduce them from a frozen lock.
- [x] **S06: Local MCP and harness parity** `risk:medium` `depends:[S05]`
  > After this: Python REST, CLI, and MCP agree on context behavior, and Codex/Claude project layouts pass discovery fixtures.
- [x] **S07: Genkit Python and OpenAI compatibility gate** `risk:high` `depends:[S06]`
  > After this: a locked executable proves the supported Genkit agent-or-flow path, OpenAI model adapter, Skills middleware, tools, structured output, streaming, sessions, aborts, and FastAPI integration.
- [x] **S08: Domain authority and shared working memory correction** `risk:high` `depends:[S06]`
  > After this: project/domain admission exposes the complete corpus, Git remains sole canonical authority, and team-wide supplemental memory has a provenance-rich lifecycle without approvers or per-record groups.
- [x] **S09: Genkit coordinator tracer** `risk:high` `depends:[S06,S07,S08]`
  > After this: a narrow diagnostic/CI probe executes one stateless hosted Genkit turn through the configured OpenAI model adapter, selected immutable skills, and project-bound read-only context tools, returning evidence-derived citations and usage in provider-neutral JSON.
- [x] **S10: Durable Genkit conversations** `risk:high` `depends:[S09]`
  > After this: ordered turns plus a bounded summary survive restart, same-conversation turns serialize, and every completed turn records the exact Git revision and skill lock it used while later turns may adopt newer canonical knowledge.
- [x] **S11: Durable mock coding jobs** `risk:high` `depends:[S10]`
  > After this: minimal `runs` and `coding_jobs` records let a mock job be atomically claimed, observed, cancelled, timed out, recovered after restart, and completed once visibly against one pinned revision.
- [ ] **S12: Mock diagnose-and-fix workflow** `risk:high` `depends:[S08,S09,S11]`
  > After this: the coordinator retrieves the fixture ADR and skill, suspends for a mock coding job, resumes with verification, and produces one mock draft-PR result.
- [ ] **S13: Supervised Codex runner** `risk:high` `depends:[S12]`
  > After this: a frozen skill lock is installed before the Codex CLI fixes the fixture in an isolated container and the supervisor independently validates its patch and tests.
- [ ] **S14: Grounded Slack conversation** `risk:high` `depends:[S10]`
  > After this: duplicate signed Slack events yield one cited reply and thread follow-ups survive restart.
- [ ] **S15: Slack diagnose-and-fix integration** `risk:high` `depends:[S13,S14]`
  > After this: one Slack request asynchronously completes the fixture coding workflow with explicit progress, failure, and mock-publication outcomes.
- [ ] **S16: Exact-revision sequential review** `risk:high` `depends:[S13]`
  > After this: a read-only review covers one exact head SHA and stale or unsupported findings cannot be published as current.
- [ ] **S20: Git publication adapter** `risk:high` `depends:[S15,S16]`
  > After this: an explicit developer request or configured automation can publish a verified branch, draft PR, or review through one provider-neutral adapter using supervisor-owned credentials and idempotent reconciliation.
- [ ] **S23: Focused v1 hardening** `risk:high` `depends:[S20]`
  > After this: the core context-to-Genkit-to-Slack-to-Codex-to-publication path passes bounded security, restart, cancellation, injection, and side-effect reconciliation suites.
- [ ] **S24: Domain adoption and CI publication guide** `risk:medium` `depends:[S20]`
  > After this: a GitHub or GitLab team can map its domain into the generic exact-SHA Git-to-Mongo publication and reconciliation contract without provider-specific kit configuration.

## Optional expansion path

These slices add useful scale or portability after core v1. They do not gate S23 hardening or S24
adoption documentation.

- [ ] **S17: Event-driven review projection** `risk:medium` `depends:[S14,S16]`
  > After this: repeated Slack events review one revision once, freeze each automation run's skill lock, and retain independently repairable publication and reaction state.
- [ ] **S18: Scheduled reconciliation** `risk:high` `depends:[S17]`
  > After this: a leased sweep recovers missed reviews and repairs projections without losing failed targets or duplicating reviews.
- [ ] **S19: Bounded parallel review** `risk:high` `depends:[S16]`
  > After this: correctness and security reviewers run in separate read-only jobs and produce one evidence-backed, restart-recoverable result.
- [ ] **S21: Genkit provider portability proof** `risk:medium` `depends:[S09]`
  > After this: configured OpenAI, Anthropic, and Gemini integrations satisfy shared contracts with explicit capability differences and no silent fallback.
- [ ] **S22: Claude harness portability proof** `risk:medium` `depends:[S13]`
  > After this: the same frozen-lock supervised coding contract executes through Claude without coordinator or workflow changes.

## Global completion checks

- `uv lock --check`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy`
- `uv run pytest`
- `uv build --all-packages`
- Slice-specific image, Compose, integration, provider-contract, security, trace, and workflow checks

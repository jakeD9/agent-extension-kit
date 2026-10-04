# Delivery Roadmap

Each slice is designed for one focused implementation session. A checkbox changes only after the
slice's acceptance criteria have fresh verification evidence. The canonical Genkit architecture
brief and the workstream artifacts under `.roadmap/workstreams/` supply fresh-session context.

- [x] **S01: Python cited-search tracer** `risk:high` `depends:[]`
  > After this: an authenticated REST request returns authorized, revision-pinned fixture knowledge from the Python service.
- [x] **S02: Persistent context boundary** `risk:medium` `depends:[S01]`
  > After this: cited retrieval survives restart and the context service cannot access runtime collections.
- [x] **S03: Authorized skill-catalog tracer** `risk:medium` `depends:[S02]`
  > After this: authenticated REST callers can list and load only authorized skills scanned from the pinned Git content pack, without a MongoDB skill catalog.
- [x] **S04: Immutable skill resolution and package API** `risk:high` `depends:[S03]`
  > After this: authorized callers can resolve named/all selections to a hashed immutable manifest and download exact self-contained packages from the current approved revision.
- [ ] **S05: Verified skill pull and lockfile CLI** `risk:high` `depends:[S04]`
  > After this: local users and automations can atomically install targeted, authorized packages and reproduce them from a frozen lock.
- [ ] **S06: Local MCP and harness parity** `risk:medium` `depends:[S05]`
  > After this: Python REST, CLI, and MCP agree on context behavior, and Codex/Claude project layouts pass discovery fixtures.
- [ ] **S07: Genkit Python and OpenAI compatibility gate** `risk:high` `depends:[S06]`
  > After this: a locked executable proves the supported Genkit agent-or-flow path, OpenAI model adapter, Skills middleware, tools, structured output, streaming, sessions, aborts, and FastAPI integration.
- [ ] **S08: Governed team memory** `risk:medium` `depends:[S06]`
  > After this: authorized callers can propose, approve, supersede, expire, and search sourced memories consistently through REST, CLI, and MCP.
- [ ] **S09: Genkit coordinator tracer** `risk:high` `depends:[S06,S07]`
  > After this: a provider-neutral runtime turn uses an authorized locked skill projection and scoped context tools to return structured cited output through the configured OpenAI model adapter.
- [ ] **S10: Durable Genkit conversations** `risk:high` `depends:[S09]`
  > After this: multi-turn state survives restart, same-thread turns serialize, and stale workers cannot update a conversation.
- [ ] **S11: Durable mock coding jobs** `risk:high` `depends:[S10]`
  > After this: a mock coding job can be submitted, observed, cancelled, recovered after restart, and completed once visibly.
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
- [ ] **S17: Event-driven review projection** `risk:medium` `depends:[S14,S16]`
  > After this: repeated Slack events review one revision once, freeze each automation run's skill lock, and retain independently repairable publication and reaction state.
- [ ] **S18: Scheduled reconciliation** `risk:high` `depends:[S17]`
  > After this: a leased sweep recovers missed reviews and repairs projections without losing failed targets or duplicating reviews.
- [ ] **S19: Bounded parallel review** `risk:high` `depends:[S16]`
  > After this: correctness and security reviewers run in separate read-only jobs and produce one evidence-backed, restart-recoverable result.
- [ ] **S20: Approval and real publication boundary** `risk:high` `depends:[S15,S19]`
  > After this: only capability-bound, independently verified results can be published using supervisor-owned credentials.
- [ ] **S21: Genkit provider portability proof** `risk:medium` `depends:[S09]`
  > After this: configured OpenAI, Anthropic, and Gemini integrations satisfy shared contracts with explicit capability differences and no silent fallback.
- [ ] **S22: Claude harness portability proof** `risk:medium` `depends:[S13]`
  > After this: the same frozen-lock supervised coding contract executes through Claude without coordinator or workflow changes.
- [ ] **S23: Security, recovery, and assembled-system proof** `risk:high` `depends:[S18,S20,S21,S22]`
  > After this: all three images pass end-to-end fixture, security, injection, cancellation, restart, and side-effect reconciliation suites.

## Global completion checks

- `uv lock --check`
- `uv run ruff check .`
- `uv run ruff format --check .`
- `uv run mypy`
- `uv run pytest`
- `uv build --all-packages`
- Slice-specific image, Compose, integration, provider-contract, security, trace, and workflow checks

# Workstream Status

- Current slice: S13 — supervised Codex execution
- State: S12 mock diagnose-and-fix workflow implemented and deterministically verified; S13 requires
  its pre-slice technical check-in before implementation
- Completed dependencies: S01, S02, S03, S04, S05, S06, S07, S08, S09, S10, S11, S12
- Verified capabilities: strict MongoDB context migrations, pinned revision synchronization,
  project/domain-admitted bounded candidate retrieval with atomic active-revision projection,
  restart-persistent cited REST search, Git/filesystem-backed skill discovery/loading without Mongo
  skill records, exact
  current-revision skill package resolution/download, verified atomic generic installation with
  frozen locks and recovery, project-scoped Codex/Claude skill layouts, snake_case REST/CLI/MCP
  parity, safe local context tools, a pinned Genkit Python/OpenAI Responses compatibility gate with
  Codex reasoning continuity, dependency-aware readiness, separate context/runtime credentials, and
  a healthy non-root `team-context` container, an authenticated local MongoDB replica set, complete
  admitted-domain knowledge/skills, team-wide supplemental working memory with Git precedence, and
  a stateless provider-neutral Genkit turn using a temporary locked skill projection plus observed
  canonical and supplemental evidence, and application-owned MongoDB conversations with ordered
  provenance-rich turns, deterministic bounded context, transactional claims/completion, restart
  recovery, and no Genkit `SessionStore`, plus minimal durable runs and mock coding jobs with pinned
  repository/content/skill inputs, transactional idempotent submission, renewable job-only leases,
  attempt fencing, separate lifecycle/outcome, bounded reconciliation, restart recovery, and a
  supervisor-owned structured completion boundary without an outbox, plus an application-owned mock
  diagnose-and-fix workflow with cited pinned submission, bounded
  terminal polling, short fenced run claims, deterministic completion/resume identities,
  effectively-once visible conversation projection, idempotent supplemental lessons, deterministic
  mock publication outcomes, and transactional consumption under an explicit jobs schema v2
- Known blockers: none
- Next eligible slice: S13. Before work starts, agree on how the persistent executor creates,
  observes, validates, and reconciles a disposable Codex environment while keeping Mongo and
  publication credentials outside the runner.
- Delivery tracks: core v1 is S09–S16, S20, S23, S24, and the final structural cleanup S25.
  S17–S19, S21, and S22 are optional expansion work and do not gate core hardening or adoption
  documentation.
- Canonical brief: `.roadmap/team-agent-architecture-brief-genkit.md`
- Skill distribution: incorporated into the canonical brief; the former addendum is archived
- Domain authority dogma: `docs/domain-knowledge-authority.md`
- Required Git-to-Mongo CI contract: `docs/required-ci-implementations.md`
- Genkit development skill: `.agents/skills/developing-genkit-python/SKILL.md`

Read the S01 through S12 `SUMMARY.md` files before the S13 technical check-in, including every
supersession notice and the replacement S08 verification evidence.

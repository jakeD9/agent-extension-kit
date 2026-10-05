# Workstream Status

- Current slice: S10 — durable Genkit conversations
- State: S09 stateless Genkit coordinator implemented, independently reviewed, and verified; S10
  requires its pre-slice technical check-in before implementation
- Completed dependencies: S01, S02, S03, S04, S05, S06, S07, S08, S09
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
  canonical and supplemental evidence
- Known blockers: none
- Next eligible slice: S10. Before work starts, agree on its ordered-turn and bounded-summary
  contracts, optional `conversation_context`, Mongo CAS/lease mechanism, compaction limits, and
  restart/concurrency proof. S09 remains a diagnostic/CI probe and does not replace local
  Codex/Claude skills and MCP workflows.
- Delivery tracks: core v1 is S09–S16, S20, S23, and S24. S17–S19, S21, and S22 are optional
  expansion work and do not gate core hardening or adoption documentation.
- Canonical brief: `.roadmap/team-agent-architecture-brief-genkit.md`
- Skills distribution requirements: `.roadmap/skills-distribution-addendum.md`
- Domain authority dogma: `docs/domain-knowledge-authority.md`
- Required Git-to-Mongo CI contract: `docs/required-ci-implementations.md`
- Genkit development skill: `.agents/skills/developing-genkit-python/SKILL.md`

Read the S01 through S09 `SUMMARY.md` files before the S10 technical check-in, including every
supersession notice and the replacement S08 verification evidence.

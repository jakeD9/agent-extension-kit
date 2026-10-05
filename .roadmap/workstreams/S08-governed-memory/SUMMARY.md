# S08 Summary — Domain Authority and Shared Working Memory

## Delivered

- Established the normative authority model for one small trusted team/domain: project admission is
  the content boundary, every admitted developer receives the complete knowledge and skill corpus,
  and a protected pinned Git revision is the sole canonical source for knowledge and skills.
- Removed per-artifact and per-memory groups, self-declared knowledge authority, content approvers,
  proposals, approval/rejection, and Mongo-backed promotion. Repository review is the only path to
  canonical content.
- Replaced governed memory with direct project-scoped shared working memory. Any admitted developer
  can create, search, update, supersede, expire, and audit it through the appropriate REST/CLI
  surfaces; MCP exposes search and additive creation. Every record is permanently
  `canonicality: supplemental` and retains provenance, evidence, author, revision, expiry,
  supersession, idempotency, and audit data.
- Added the v5 `shared_*` MongoDB collections. Legacy approval-era data is never deleted or silently
  reinterpreted; nonempty legacy collections fail readiness with an actionable migration boundary.
- Rebuilt canonical knowledge persistence around revision- and projection-hash-scoped staging,
  completeness verification, expected-predecessor compare-and-set activation, source-bound bounded
  retrieval, and explicit uncertain-activation reconciliation. Readers observe one complete active
  projection that exactly matches the loaded skill catalog revision.
- Made production startup read-only for publication and fail closed on mutable revisions,
  missing/mismatched active projections, or readiness failures. Explicit local fixture bootstrap
  uses the same stage/verify/activate path and hashes mutable same-label content.
- Added the domain-authority dogma, platform-neutral required-CI contract, downstream roadmap audit,
  and S24 adoption/documentation slice for GitHub/GitLab team implementations.

## Verification evidence

- The final deterministic suite passed 202 tests with three explicitly opt-in integrations skipped.
- Ruff lint/format, strict mypy over 19 source files, Compose validation, `git diff --check`, and
  builds for all eight Python distributions passed.
- Review regressions cover equal same-project access across identities/groups, outsider hiding,
  direct supplemental-memory lifecycle and replay authorization, legacy-data fail-close, bounded
  source-isolated retrieval, revision mismatch route blocking, stale publisher rejection, ambiguous
  activation reconciliation, and mutable same-count content refresh.
- Independent review completed clean after correction rounds; its final focused suite passed 47
  tests and confirmed documentation matches projection-hash and uncertainty behavior.

## S09 inputs and boundaries

- The coordinator must keep canonical Git knowledge and supplemental working memory as separate
  evidence channels, cite both, and follow/surface Git when they conflict.
- Context and skill access is project/domain-wide after admission. Capability controls still govern
  repository/runtime side effects; they are not content authority.
- Genkit receives only a selected installer-produced skill projection from the complete project
  catalog. The knowledge projection, skill catalog, and recorded run lock must name the same exact
  Git revision.
- No model tool can promote memory. Durable lessons become canonical only through an ordinary Git
  branch, commit, pull/merge request, review, and protected-branch merge.
- Conversation/session state remains excluded. S09 owns the stateless coordinator tracer; S10 owns
  durable Genkit conversations.

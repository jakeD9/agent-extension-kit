# Review: Team Agent Architecture (Genkit Python Runtime)

> **Archived review input.** Accepted decisions were reconciled into the canonical Genkit
> architecture brief and active workstream briefs. This review is not an implementation contract.

Reviewed document: [`.roadmap/team-agent-architecture-brief-genkit.md`](https://github.com/jakeD9/agent-extension-kit/blob/main/.roadmap/team-agent-architecture-brief-genkit.md)

Scope note: `skills-distribution-addendum.md` and `docs/domain-knowledge-authority.md` were not reviewed directly. This review judges the brief against the dogma as the brief summarizes it: one small, trusted team, domain admission as the only content boundary, Git as the canonical source, and durable execution outside the model loop.

## Overall

The core shape is sound. The `AgentRuntime` boundary, Codex/Claude as the coding harness instead of a homemade loop, durable state outside the model, deterministic URL allowlisting, credentials kept out of the harness, emoji as a projection, and the bans on swarms, LangGraph and K8s manifests are all right.

The overengineering is concentrated in a few places, and most of it comes from designing for multi-replica, multi-tenant conditions that the dogma explicitly rules out. The issues below are ordered roughly by how much they would simplify the design.

## 1. Skill distribution is solving a problem Git already solves

Section 4 describes an in-memory catalog, versioned manifests, `/v1/skills:resolve`, package IDs, a `SkillInstaller` with hash verification, a snake_case lock plus an ownership manifest, atomic per-run projections, and a "verified cache" for old frozen locks. That last item exists only because the service serves just the current revision.

For a trusted team where skills live in Git, the lock can simply be a commit SHA. Runners and the hosted runtime check out `extension/skills` at that SHA. Old revisions are free, content addressing is free, and the resolve/package/cache layers disappear.

**Recommendation:** Keep `team-agent skills pull` as a thin convenience for local project setup, and drop the rest. If the addendum requires the package API for a reason the brief doesn't state, write that reason down, because it isn't evident here.

## 2. Distributed concurrency control assumes replicas you probably don't need

The brief calls for CAS revisions, conversation leases, fencing tokens, renewable worker leases, three separately deployable process modes, and the statement that "a process-local lock is insufficient." All of that is only true if you run multiple workers. For one team's Slack traffic, a single worker process would handle the load easily.

**Recommendation:** Make one worker replica the v1 deployment rule. Serialize turns with a per-thread asyncio lock, and keep the cheap, high-value pieces: unique indexes for Slack event dedup and idempotency keys on external side effects (PR creation, review publication, Slack posts). Fencing and leases can come back if you ever scale out, and the interfaces don't need to change for that.

## 3. Shared working memory has a full CMS lifecycle

Memory records have provenance, evidence, author, revision, expiration, supersession, a separate idempotency collection, an audit-events collection, an audit endpoint, and MCP restricted to additive operations. That last restriction is an odd one for a team where everyone is trusted equally.

Memory is also supplemental by design and is promoted only through Git review, so it competes conceptually with the dogma.

**Recommendation:** Either defer memory entirely or ship it as create/search/expire with author, timestamp, and source link. "Prepare a Git change" may cover most of the real need anyway.

## 4. RAG is built to production-search standards before anyone knows it's needed

The plan includes hybrid lexical and vector search, optional reranking, revision-scoped projections with an atomic pointer, and nine provenance fields per chunk. An ADR, conventions, and glossary corpus for one team may be small enough that lexical search is sufficient. Agents with file access can often just read it.

**Recommendation:** Start with Atlas lexical search plus SHA and path citations, then add vectors when the retrieval evals show misses. The "rebuild into a new collection, then swap the pointer" step is cheap, so keep that. Commit SHA plus path is enough provenance; the rest can be derived from it.

## 5. Use Genkit's fallbacks as the plan, not as contingencies

The application already owns history, durability, orchestration, and skill selection. What Genkit actually contributes is the model abstraction, the tool loop, and tracing, and its Python version is preview with a beta Agents API. Using `define_agent`, session stores, and Skills middleware creates a second state holder and a directory-projection dance, all to save very little.

**Recommendation:** Go straight to `generate()` with tools, application-owned history, and selected skills rendered into instructions. Each of those is already a documented fallback in the brief, and making them the default shrinks Phase 0 considerably.

Separately, the coordinator doesn't need a "Codex-capable" model. It routes and retrieves, and the coding happens in the harness.

## 6. The package layout is too fine-grained for v1

Ten packages and four apps in a uv workspace means ten `pyproject` files and ten import boundaries to maintain before any of those boundaries has proven useful.

**Recommendation:** Start with three packages:

- `contracts`
- `context_client`, which local tools share
- one `team_agent` package, with `workflows`, `jobs`, `automations`, `git_providers`, and `observability` as subpackages

Split them out later where a real boundary emerges.

## 7. State models mix lifecycle with outcome

Jobs have seven terminal states. `no_fix_found` and `unsafe_to_proceed` are results of a job that completed, not lifecycle states.

**Recommendation:** Collapse them to `completed / failed / timed_out / cancelled / needs_input` plus an `outcome` field.

Similarly, automations define both a logical review identity and an optional canonical review key. Pick one, which is probably `(repo, MR, head_sha)`, and treat the Slack message as projection metadata.

## 8. Phase order front-loads infrastructure

Phase 1 builds the skill lock system, memory, MCP/CLI/REST parity, and the full RAG pipeline before anything works end to end.

**Recommendation:** Move the vertical slice earlier: Slack or CLI, then the coordinator, then lexical retrieval, then skills at a SHA, then a Codex job in a container, then supervisor checks, then a mock draft PR. That's essentially the current Phase 3 exit criterion, and it tells you which of the deferred pieces you actually miss.

## 9. Document sprawl is a hazard for an AI implementer

The brief, the normative addendum that partially supersedes it, the normative dogma doc, `required-ci-implementations.md`, and undefined slice IDs (S09, S10, S17–S18, S24) leave a lot to reconcile. Codex will try to satisfy all of these documents at once, and conflicting normative documents are how you get the overbuilt version.

**Recommendation:** Fold the addendum's binding parts into the brief, or add a short precedence table, and define or remove the slice IDs.

## What to keep as-is

These parts look like complexity but are earning their place:

- **Separate context service.** Local agents need knowledge access without database credentials.
- **Credential and verification separation.** Keep the harness container free of Git write credentials, run checks in a fresh container from the extracted patch, and let the supervisor publish.
- **Idempotency and reconciliation on external actions.** A network timeout really can mean the action succeeded.
- **Suspended runs instead of long-held requests.** Don't keep a request open for a 30-minute job.
- **Docker-label reconciliation for orphaned containers.**
- **Optional status for parallel review and automations.**

## Highest-leverage change

If you take only one change, make it the single-worker rule in #2, combined with SHA-as-lock in #1. Together those remove probably a third of the coordination machinery without touching any security property.

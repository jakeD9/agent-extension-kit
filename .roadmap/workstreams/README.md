# Workstream Session Protocol

These artifacts let an engineer start any roadmap slice without prior chat history. Document
precedence is intentionally narrow: domain-authority dogma first, then the canonical Genkit brief,
then the active slice brief. `docs/` describes as-built behavior. Summaries and `archive/` are
historical evidence, never competing implementation contracts.

1. Read the canonical Genkit architecture brief, the normative domain-authority dogma,
   `STATUS.md`, the selected slice's `BRIEF.md`, and every dependency's `SUMMARY.md`. Historical
   summaries with supersession notices are evidence, not current contracts.
2. For S07, S09, S10, S19, S21, or S23, also read
   `.agents/skills/developing-genkit-python/SKILL.md` and only the references it routes to for the
   slice. Treat the installed skill as implementation guidance, not as runtime authorization.
3. Before editing, hold a technical check-in with the team. Cover the observable outcome; concrete
   types, commands, endpoints, collections, and modules; mechanism and data flow; whether MongoDB is
   necessary; failure, restart, and idempotency behavior; actual safety boundaries; explicit
   exclusions and complexity budget; verification; and decisions still requiring the team. Do not
   implement until the team explicitly approves this boundary.
4. Confirm dependencies are complete and restate the approved slice boundary.
5. Implement only the vertical behavior named in the brief. New shared contracts must remain
   provider-neutral.
6. Run every listed verification command after the last change. Distinguish mock, recorded, and
   live checks.
7. Write `SUMMARY.md` and update `STATUS.md` and the root roadmap. If interrupted, write
   `CONTINUE.md` instead and do not mark the slice complete.

Across every slice, treat the protected pinned Git revision as the sole canonical knowledge/skill
source and Mongo knowledge as a rebuildable projection. Project/domain admission is the content
boundary for this trusted-team kit. Shared Mongo working memory is supplemental and team-wide;
external side effects still require an explicit developer request or configured automation and the
minimum required credentials, but no internal approval bureaucracy.

Summaries record outcomes and evidence, not a transcript. A new session should be able to identify
what is safe to consume, what remains stubbed, and exactly which slice is next.

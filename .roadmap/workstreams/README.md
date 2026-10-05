# Workstream Session Protocol

These artifacts let an engineer start any roadmap slice without prior chat history.

1. Read the canonical Genkit architecture brief, the normative domain-authority dogma,
   `STATUS.md`, the selected slice's `BRIEF.md`, and every dependency's `SUMMARY.md`. Historical
   summaries with supersession notices are evidence, not current contracts.
2. For S07, S09, S10, S19, S21, or S23, also read
   `.agents/skills/developing-genkit-python/SKILL.md` and only the references it routes to for the
   slice. Treat the installed skill as implementation guidance, not as runtime authorization.
3. Confirm dependencies are complete and restate the slice boundary before editing.
4. Implement only the vertical behavior named in the brief. New shared contracts must remain
   provider-neutral.
5. Run every listed verification command after the last change. Distinguish mock, recorded, and
   live checks.
6. Write `SUMMARY.md` and update `STATUS.md` and the root roadmap. If interrupted, write
   `CONTINUE.md` instead and do not mark the slice complete.

Across every slice, treat the protected pinned Git revision as the sole canonical knowledge/skill
source and Mongo knowledge as a rebuildable projection. Project/domain admission is the content
boundary for this trusted-team kit. Shared Mongo working memory is supplemental and team-wide;
external-action approvals remain separate.

Summaries record outcomes and evidence, not a transcript. A new session should be able to identify
what is safe to consume, what remains stubbed, and exactly which slice is next.

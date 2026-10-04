# Workstream Session Protocol

These artifacts let an engineer start any roadmap slice without prior chat history.

1. Read the canonical Genkit architecture brief, `STATUS.md`, the selected slice's `BRIEF.md`, and
   every dependency's `SUMMARY.md`.
2. For S05, S07, S08, S17, S19, or S21, also read
   `.agents/skills/developing-genkit-python/SKILL.md` and only the references it routes to for the
   slice. Treat the installed skill as implementation guidance, not as runtime authorization.
3. Confirm dependencies are complete and restate the slice boundary before editing.
4. Implement only the vertical behavior named in the brief. New shared contracts must remain
   provider-neutral.
5. Run every listed verification command after the last change. Distinguish mock, recorded, and
   live checks.
6. Write `SUMMARY.md` and update `STATUS.md` and the root roadmap. If interrupted, write
   `CONTINUE.md` instead and do not mark the slice complete.

Summaries record outcomes and evidence, not a transcript. A new session should be able to identify
what is safe to consume, what remains stubbed, and exactly which slice is next.

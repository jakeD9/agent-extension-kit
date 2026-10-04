# S12 — Mock Diagnose-and-Fix Workflow

Depends on S08, S09, and S11. Exercise the fixture incident end to end: the coordinator retrieves
the ADR and authorized `diagnose-and-fix` skill, persists workflow stages, submits one mock coding
job, suspends, consumes a validated completion, resumes the logical conversation, produces one mock
draft-PR outcome, and proposes any reusable lesson as non-authoritative memory. Exclude real Docker,
Codex, Git credentials, and Slack. Verify restart at every persisted boundary, duplicate delivery,
needs-input/approval states, and no repeated external action.

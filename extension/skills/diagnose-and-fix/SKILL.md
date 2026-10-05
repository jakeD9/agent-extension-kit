---
name: diagnose-and-fix
description: Use when an error or incident must be diagnosed and repaired in a repository.
version: "1"
projects: [event-ingestion]
allowedTools: [search_team_knowledge, get_team_document]
---
# Diagnose and fix

## Procedure

1. Reproduce the reported failure without modifying production systems.
2. Retrieve applicable approved architecture and incident knowledge.
3. Identify the narrowest root cause supported by evidence.
4. Add a failing regression test, implement the smallest safe fix, and rerun verification.
5. Return the diagnosis, changed paths, verification evidence, uncertainty, and a supplemental
   working-memory suggestion when appropriate. If it should become canonical, prepare a separate Git
   change for normal team review.

Completion requires a reproducible diagnosis and passing independent verification. Never merge or deploy.

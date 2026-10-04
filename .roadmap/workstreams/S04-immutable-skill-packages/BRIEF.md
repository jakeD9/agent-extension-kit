# S04 — Immutable Skill Resolution and Package API

Depends on S03. Extend the Git/filesystem catalog to validate complete self-contained skill
packages, referenced resources, safe relative paths, bounded files, and hashes. Add
provider-neutral versioned resolution/lock contracts plus authenticated `POST /v1/skills:resolve`
and exact `GET /v1/skill-packages/{package_id}` endpoints. Named and all selections resolve only the
caller's authorized project catalog at the current immutable revision; package IDs never bypass
authorization and unavailable revisions fail instead of selecting newer content. The service makes
no historical retention promise. Use snake_case JSON contracts. Exclude installation, CLI, MCP,
Genkit, and coding jobs. Verify hashes, bounded resource inventory, traversal/symlink rejection,
authorization-before-revision behavior, immutable revision behavior, and canonical API failures
without a MongoDB skill collection.

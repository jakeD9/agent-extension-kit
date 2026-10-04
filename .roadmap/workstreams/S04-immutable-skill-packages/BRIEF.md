# S04 — Immutable Skill Resolution and Package API

Depends on S03. Extend the Git/filesystem catalog to validate complete skill packages, referenced
resources, safe relative paths, dependency declarations, hashes, and dependency cycles. Add
provider-neutral versioned resolution/lock contracts plus authenticated `POST /v1/skills:resolve`
and exact `GET /v1/skill-packages/{package_id}` endpoints. Named and all selections resolve only the
caller's authorized project catalog at one immutable revision; package IDs never bypass authorization
and unavailable revisions fail instead of selecting newer content. Preserve packages for the
supported active-job/retry lifetime. Exclude installation, CLI, MCP, Genkit, and coding jobs. Verify
hashes, resource inventory, dependencies, cycles, traversal rejection, authorization, immutable
revision behavior, and canonical API failures without a MongoDB skill collection.

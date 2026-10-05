# S04 Summary — Immutable Skill Resolution and Package API

> Historical delivery evidence. Package integrity remains applicable, while its per-artifact scope
> assumptions are superseded by project/domain admission.

## Delivered

- Added versioned snake_case contracts for skill resolution manifests, immutable package manifests,
  and exact package bundles; portable schema version `"1"` rejects unsupported values.
- Extended the Git content-pack loader to inventory complete self-contained skill directories,
  validate explicit resources and safe paths, reject symlinks and dependency declarations, and hash
  raw bytes using bounded streaming reads.
- Added authenticated `POST /v1/skills:resolve` and
  `GET /v1/skill-packages/{package_id}` endpoints with authorization-constrained named/all
  selection, current-revision enforcement, and nondisclosing not-found behavior.
- Made catalog revision explicit even for an empty catalog, bound pagination cursors to that
  revision, and kept MongoDB documents and Python wire output in snake_case.
- Defined the lifecycle boundary: the service serves only its current approved catalog; later jobs
  stage verified packages for their lifetime, and old frozen locks require a verified cache or fail
  without substituting current content.

## Verification evidence

- Deterministic tests cover package inventory and hashes, size/file-count bounds, growth after
  `stat`, unsafe resources, symlinks, unsupported schemas, unauthorized revision probing, exact
  package authorization, empty catalogs, stale cursors, and snake_case JSON round trips.
- Ruff lint/format, strict mypy, lockfile consistency, the full deterministic test suite, diff
  checks, and builds for all Python distributions passed immediately before the S04 commit.
- The opt-in live-Mongo integration test remains skipped unless a test MongoDB URI is configured.
- No installer, CLI, MCP adapter, Genkit runtime, coding job, or historical package store was added
  or claimed.

## S05 inputs and constraints

- Build one shared Python client/installer used by both CLI and later executors; do not spawn the CLI
  from services.
- Validate every downloaded file against the locked path, size, and SHA-256 before atomic install;
  execute no package scripts.
- Preserve unmanaged files and unselected managed packages, reject collisions, and keep lockfiles
  credential-free and snake_case.
- Frozen pulls use only exact lock identities from the current service or a verified cache and never
  resolve or substitute latest.

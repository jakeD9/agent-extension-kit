# Context REST API

## Contract

The context service exposes an internal, additive `/v1` HTTP API to the hosted runtime and later
CLI/MCP adapters. Callers authenticate with `Authorization: Bearer <token>`; the configured
authenticator resolves the token to a principal whose project and group scopes are enforced again
inside the context application service. All operations in this document are read-only and safe to
retry.

Every response includes `X-Request-ID`. Successful JSON responses include the same value as
`request_id`. Python REST, JSON, and lockfile contracts use snake_case. Errors use one envelope:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "The skill list request is invalid",
    "request_id": "request-id"
  }
}
```

Missing or invalid credentials return `401 unauthorized`. Invalid input returns
`422 validation_failed`. Unexpected server failures return `500 internal_error` without internal
paths or stack traces. Additive response fields may appear within `/v1`; breaking changes require a
new path version. S04 intentionally resets the not-yet-released `/v1` fixture responses from legacy
TypeScript-style camelCase to snake_case before any supported external consumer exists; after this
pre-release reset, the versioning rule applies normally.

## Skill catalog

### `GET /v1/skills`

Lists skill metadata authorized for the caller. Query parameters:

- `project` (required): project scope to search.
- `limit` (optional): integer from 1 through 50; defaults to 50.
- `cursor` (optional): opaque continuation value returned by an earlier request for the same
  project and catalog revision. Malformed, unsupported, cross-project, or stale-revision cursors
  return 422.

The service builds the catalog from its configured pinned Git/filesystem content pack and applies
project and group authorization before sorting or pagination. A project outside the principal's
scope returns an empty page, which does not disclose whether skills exist there.

```json
{
  "items": [
    {
      "name": "diagnose-and-fix",
      "description": "Use when an error or incident must be diagnosed and repaired.",
      "version": "1",
      "projects": ["event-ingestion"],
      "access_groups": ["engineering"],
      "allowed_tools": ["search_team_knowledge"],
      "citation": {
        "repository": "agent-extension-kit-sample",
        "path": "skills/diagnose-and-fix/SKILL.md",
        "revision": "abc123"
      }
    }
  ],
  "next_cursor": "opaque-value-if-more-items-exist",
  "request_id": "request-id"
}
```

Skill bodies are absent from list responses. `next_cursor` is omitted on the final page.

### `GET /v1/skills/{name}`

Loads one authorized skill body. The `project` query parameter is required; `revision` optionally
requires an exact available catalog revision. The response contains
the list metadata, `body`, and repository/path/revision provenance. A nonexistent name and a real
name outside the caller's authorized scope both return the identical `404 skill_not_found`
contract, preventing name discovery across authorization boundaries.

An unavailable requested revision returns `404 skill_revision_not_found`; the service never falls
forward to newer content.

### `POST /v1/skills:resolve`

Resolves exactly one selection mode: a nonempty kebab-case `names` array or `all: true`. `project` is
required and `revision` may require the exact currently loaded revision. Authorization is resolved
before revision-specific errors. The result records `catalog_revision`, the explicit
`selected_names`, and the self-contained immutable package manifests authorized for the caller.
`all` means every authorized skill for that project, not every skill in the source checkout. Missing
or unauthorized named skills share `404 skill_not_found`.

Each package manifest includes its `sha256:` package ID, source revision, description, version,
explicit resources, citation, and sorted file inventory with raw-byte SHA-256 hashes and sizes. V1
packages are self-contained and have no skill-to-skill dependency graph. The content ID covers
repository and revision provenance, skill identity, and the complete inventory. `schema_version` is
exactly `"1"`; unsupported versions are rejected.

### `GET /v1/skill-packages/{package_id}`

Downloads the exact authorized immutable package for the required `project`, as a JSON bundle whose
files contain relative paths, sizes, hashes, and base64 content. Invalid IDs return 422. Missing and
unauthorized package IDs share `404 skill_package_not_found`; knowing an ID never bypasses scope.
Authentication occurs before request validation on both package endpoints.

The service serves only the current approved catalog revision; it does not retain historical
packages for jobs. A runner stages verified packages during job bootstrap for that job's lifetime.
A frozen lock may use a verified local/job cache, but missing old packages fail without substituting
the current revision.

## Knowledge search

`POST /v1/knowledge/search` retains the S01/S02 contract: a validated body containing `query`,
`project`, and a bounded optional `limit`, with authorized revision-pinned results and citations.
It is read-only and safe to retry.

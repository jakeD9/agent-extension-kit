# Context REST API

## Contract

The context service exposes an internal, additive `/v1` HTTP API to the hosted runtime and later
CLI/MCP adapters. Callers authenticate with `Authorization: Bearer <token>`; the configured
authenticator resolves the token to a principal whose project/domain admission is enforced again
inside the context application service. Admission grants access to the complete domain corpus;
there are no per-artifact groups. Memory mutations require a bounded `Idempotency-Key`; update and
expiry requests also require the current `expected_revision`.

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

Lists domain skill metadata for an admitted caller. Query parameters:

- `project` (required): project scope to search.
- `limit` (optional): integer from 1 through 50; defaults to 50.
- `cursor` (optional): opaque continuation value returned by an earlier request for the same
  project and catalog revision. Malformed, unsupported, cross-project, or stale-revision cursors
  return 422.

The service builds the catalog from its configured pinned Git/filesystem content pack and checks
project/domain admission before sorting or pagination. An admitted caller sees the complete domain
catalog. A project outside the principal's scope returns an empty page, which does not disclose
whether skills exist there.

```json
{
  "items": [
    {
      "name": "diagnose-and-fix",
      "description": "Use when an error or incident must be diagnosed and repaired.",
      "version": "1",
      "projects": ["event-ingestion"],
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

Loads one domain skill body. The `project` query parameter is required; `revision` optionally
requires an exact available catalog revision. The response contains
the list metadata, `body`, and repository/path/revision provenance. A nonexistent name and a real
name outside the caller's admitted domain both return the identical `404 skill_not_found`
contract, preventing name discovery across domain boundaries.

An unavailable requested revision returns `404 skill_revision_not_found`; the service never falls
forward to newer content.

### `POST /v1/skills:resolve`

Resolves exactly one selection mode: a nonempty kebab-case `names` array or `all: true`. `project` is
required and `revision` may require the exact currently loaded revision. Admission is resolved
before revision-specific errors. The result records `catalog_revision`, the explicit
`selected_names`, and the self-contained immutable package manifests for the domain.
`all` means every skill for that project/domain, not every skill in another deployment. Missing
or non-admitted named skills share `404 skill_not_found`.

Each package manifest includes its `sha256:` package ID, source revision, description, version,
explicit resources, citation, and sorted file inventory with raw-byte SHA-256 hashes and sizes. V1
packages are self-contained and have no skill-to-skill dependency graph. The content ID covers
repository and revision provenance, skill identity, and the complete inventory. `schema_version` is
exactly `"1"`; unsupported versions are rejected.

### `GET /v1/skill-packages/{package_id}`

Downloads the exact domain immutable package for the required `project`, as a JSON bundle whose
files contain relative paths, sizes, hashes, and base64 content. Invalid IDs return 422. Missing and
unauthorized package IDs share `404 skill_package_not_found`; knowing an ID never bypasses scope.
Authentication occurs before request validation on both package endpoints.

The service serves only the current canonical catalog revision; it does not retain historical
packages for jobs. A runner stages verified packages during job bootstrap for that job's lifetime.
A frozen lock may use a verified local/job cache, but missing old packages fail without substituting
the current revision.

## Knowledge search

`POST /v1/knowledge/search` retains the S01/S02 contract: a validated body containing `query`,
`project`, and a bounded optional `limit`, with domain-admitted revision-pinned results and citations.
It is read-only and safe to retry.

## Shared working memory

The corrected S08 contract provides:

- `POST /v1/memories/search` returns current, unexpired, unsuperseded supplemental memories for an
  admitted project/domain. It uses a maximum limit of 50 and a query/project-bound opaque cursor.
- `POST /v1/memories` creates immediately team-visible working memory and returns 201 plus
  `Location`. The body preserves project, title/body, provenance, evidence, expiry, and optional
  `supersedes_memory_id`. Supersession commits atomically. An exact retry returns the same memory;
  key reuse with a different payload returns `409 memory_conflict`.
- `PUT /v1/memories/{id}` replaces mutable content using an idempotency key and
  `expected_revision` optimistic concurrency.
- `POST /v1/memories/{id}/expire` retires current memory using the same idempotency and
  optimistic-concurrency rules.
- `GET /v1/memories/{id}/audit` returns bounded, cursor-paginated immutable lifecycle events.

Every record has fixed `canonicality: supplemental`. There is no approve/reject operation, content
approver role, or per-record audience. Missing and domain-hidden memory IDs share the same 404
contract. MCP exposes only `search_team_memory` and `create_team_memory`; update, expire, and audit
remain on REST and the human/operator CLI. No transport can make memory canonical. OpenAPI at
`/openapi.json` documents the named v1 request and response schemas. This is a pre-release corrective
reset; after it lands, breaking changes require `/v2`.

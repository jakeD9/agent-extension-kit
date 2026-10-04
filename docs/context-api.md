# Context REST API

## Contract

The context service exposes an internal, additive `/v1` HTTP API to the hosted runtime and later
CLI/MCP adapters. Callers authenticate with `Authorization: Bearer <token>`; the configured
authenticator resolves the token to a principal whose project and group scopes are enforced again
inside the context application service. All operations in this document are read-only and safe to
retry.

Every response includes `X-Request-ID`. Successful JSON responses include the same value as
`requestId`. Errors use one envelope:

```json
{
  "error": {
    "code": "validation_failed",
    "message": "The skill list request is invalid",
    "requestId": "request-id"
  }
}
```

Missing or invalid credentials return `401 unauthorized`. Invalid input returns
`422 validation_failed`. Unexpected server failures return `500 internal_error` without internal
paths or stack traces. Additive response fields may appear within `/v1`; breaking changes require a
new path version.

## Skill catalog

### `GET /v1/skills`

Lists skill metadata authorized for the caller. Query parameters:

- `project` (required): project scope to search.
- `limit` (optional): integer from 1 through 50; defaults to 50.
- `cursor` (optional): opaque continuation value returned by an earlier request for the same
  project. Malformed, unsupported, or cross-project cursors return 422.

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
      "accessGroups": ["engineering"],
      "allowedTools": ["search_team_knowledge"],
      "citation": {
        "repository": "agent-extension-kit-sample",
        "path": "skills/diagnose-and-fix/SKILL.md",
        "revision": "abc123"
      }
    }
  ],
  "nextCursor": "opaque-value-if-more-items-exist",
  "requestId": "request-id"
}
```

Skill bodies are absent from list responses. `nextCursor` is omitted on the final page.

### `GET /v1/skills/{name}`

Loads one authorized skill body. The `project` query parameter is required. The response contains
the list metadata, `body`, and repository/path/revision provenance. A nonexistent name and a real
name outside the caller's authorized scope both return the identical `404 skill_not_found`
contract, preventing name discovery across authorization boundaries.

S03 serves the one revision loaded at startup. Immutable selection manifests, requested available
revisions, and complete package downloads are introduced by S04 as specified in the skills
distribution addendum; they do not require a MongoDB skill catalog.

## Knowledge search

`POST /v1/knowledge/search` retains the S01/S02 contract: a validated body containing `query`,
`project`, and a bounded optional `limit`, with authorized revision-pinned results and citations.
It is read-only and safe to retry.

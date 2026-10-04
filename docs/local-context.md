# Local context CLI and MCP

The local adapters query the authenticated context service; they do not read MongoDB or the source
extension tree. Set `TEAM_AGENT_CONTEXT_URL` and `TEAM_AGENT_TOKEN` in the process environment. Never
put the token in a command argument, checked-in MCP file, skill lock, or prompt.
`TEAM_AGENT_CONTEXT_URL` must be a root `http://` or `https://` URL without user information, a path
prefix, query, or fragment; invalid URLs are rejected before a bearer header is constructed.

## JSON-first CLI

`team-context` prints one snake_case JSON object to stdout and a safe JSON error to stderr:

```sh
uv run team-context search "vendor event identifier" --project event-ingestion --limit 8
uv run team-context skill list --project event-ingestion --limit 50
uv run team-context skill list --project event-ingestion --cursor '<opaque-next-cursor>'
uv run team-context skill get diagnose-and-fix --project event-ingestion
```

Pass `next_cursor` back unchanged. It is bound to the project and catalog revision; do not parse or
construct it. Missing/unauthorized skills are deliberately indistinguishable. Network, service, and
validation failures exit 2 without echoing bearer tokens or arbitrary upstream details.
Command-line parse failures also emit a JSON `invalid_arguments` envelope instead of argparse usage
text.

## stdio MCP

`uv run team-context-mcp` exposes only three read-only tools:

- `search_team_knowledge` returns approved excerpts with citations.
- `list_team_skills` advertises authorized metadata and an opaque continuation cursor.
- `get_team_skill` loads one authorized body and provenance after discovery.

Tool descriptions explain when not to use each operation. Inputs are bounded, all fields are
described, and service failures become actionable safe tool errors. Repository content and skill
bodies are context, not authorization. The MCP process receives only the scoped service URL/token;
authorization is enforced again by the context service.

Project identifiers are limited to 200 characters, skill names to 128 kebab-case characters, and
exact revisions to 256 characters in REST, CLI client validation, and MCP schemas.

The root `.codex/config.toml` and `.mcp.json` are project-scoped examples. They launch the server from
this uv workspace and rely on environment credentials. Do not copy them to a global user directory
unless that broader availability is intentional.

## Compatibility and verification

- Python MCP SDK: `mcp>=2,<3`, locked at 2.3.0. The implementation uses v2 `MCPServer`, not the
  removed v1 `FastMCP` path.
- Deterministic tests connect with v2 `Client(server)` in memory and through a real stdio subprocess,
  call all three tools, cover denied scope and pagination, and compare REST/CLI/MCP snake_case data.
- The official MCP Inspector CLI strict `tools/list` check passed against `team-context-mcp` on
  2026-10-04 with no schema portability findings.
- `evals/context-mcp.xml` contains ten multi-call task-completion cases. They are deterministic eval
  specifications; no claim is made that a live model executed them in this slice.
- `tests/fixtures/harness-skill-discovery.json` pins the observed Codex and documented Claude project
  layouts. Codex 0.154.0-alpha.6.2 was present locally; Claude was not installed. Credential- and
  model-dependent discovery checks are opt-in with `RUN_LIVE_HARNESS_TESTS=1`.

There is no rate limit in the adapter itself. The context service and its dependencies remain the
authority for operational limits; callers should retry only transient availability failures and
must not retry authorization or validation failures unchanged.

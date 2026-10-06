# Addendum: Git-Backed Skills and CLI Distribution

> **Archived design input.** Its binding decisions are incorporated into the canonical Genkit
> architecture brief. This file is retained only as the historical distribution specification.

Date: 2026-10-04
Applies to: `team-agent-architecture-brief.md` and `team-agent-architecture-brief-genkit.md`
Status: Implementation requirements; commands are proposed interfaces.

This addendum is subordinate to the domain-authority dogma in
`docs/domain-knowledge-authority.md`: one deployment serves one trusted team/domain, and every
admitted developer can use and contribute the complete domain skill catalog. “Authorized” below
means admitted to that project/domain; it does not permit per-skill groups or approvers.

## Purpose and decisions

Add a Python CLI command that downloads selected skills or the complete domain catalog for coding
runners, local developers, and automations. Keep canonical skill packages in a protected, pinned Git
revision and build discovery metadata in memory in the context service. A skill change becomes
canonical only through branch/commit/PR/team review. MongoDB remains responsible for runtime
records, supplemental shared working memory, and rebuildable knowledge indexes; it is not required
to hold a skill catalog.

This addendum replaces any earlier requirement to persist available skill definitions in MongoDB. Existing `list_team_skills` and `get_team_skill` agent tools remain available and use the Git-backed catalog. Downloading skills and selecting which instructions an agent receives are separate operations.

## Git-backed skill catalog and CLI distribution

The context service scans canonical skill packages from a pinned Git checkout or image-bundled
content at startup. Validate frontmatter, package names, referenced files, and catalog consistency;
build the discovery index in memory. Skill content and catalog metadata are rebuildable from Git,
not authoritative MongoDB records. Project/domain admission grants access to the complete catalog;
do not filter individual skills by groups.

Implement these project CLI commands:

```bash
# Inspect the catalog without installing packages.
team-agent skills list --project safety-platform

# Pull one or several selected skills into a project-local directory.
team-agent skills pull review-merge-request diagnose-and-fix \
  --project safety-platform --dest .team-agent/skills

# Pull the complete catalog for an admitted caller in this project/domain.
team-agent skills pull --all \
  --project safety-platform --dest .team-agent/skills

# Install selected skills into a harness-specific project format.
team-agent skills pull review-merge-request \
  --project safety-platform --target claude --project-root .

# Reproduce exactly the packages selected for a coding job or workflow.
team-agent skills pull --lock /job/input/skills.lock.json \
  --dest /workspace/.team-agent/skills --frozen --non-interactive
```

These commands are proposed application interfaces, not Genkit built-ins. Use the same Python SkillClient/SkillInstaller underneath the CLI and executor, so services need not spawn a CLI subprocess.

Exactly one selection mode is required: named skills, `--all`, or `--lock`. `--revision`
optionally requires the exact current canonical Git revision; the service does not browse history.
Without it, resolve the current catalog once per pull. `--all` means the full domain catalog for
the supplied scope, not every company skill. It does not automatically enable every downloaded skill
for every agent. Named and all pulls require `--project`. Lock pulls derive the project from the
validated lock; an optional explicit `--project` must match it.

`skills:resolve` accepts a selection and project scope and returns a manifest pinned to the current
canonical catalog revision. Entries include skill name, package ID, source revision, file hashes,
description, resource inventory, and schema version. Packages are self-contained; v1 has no
skill-to-skill dependency graph. Python REST, JSON, and lockfile fields use `snake_case`. Package
endpoints serve the exact current revision. Authentication and scope enforcement use the context
service, and no client receives MongoDB credentials.

Download the full selected package, including references and scripts. Fetch into a temporary
directory, validate hashes and safe relative paths, and atomically install each package. Do not
execute downloaded scripts during installation.

Write `skills.lock.json` beside the installed catalog with selected package identities and hashes.
`--frozen` uses only the supplied lock, rejects missing or mismatched packages, and never substitutes
latest. The context service serves only its current canonical revision and does not promise historical
package retention. A coding job stages verified packages during bootstrap and keeps them for that
job's lifetime. A frozen lock may use a verified local or job cache; if neither the cache nor current
service can supply the exact package, the pull fails.

Default pulls update CLI-managed packages only. Preserve user-authored files and unselected packages. Keep an ownership manifest and reject collisions with unmanaged files. An explicit future `--prune` option may remove only owned packages excluded from the resolved selection. Fail nonzero with actionable errors; support `--json` output for automation, and keep credentials out of arguments and manifests.

`--target` supports `generic`, `codex`, and `claude`; default to generic. Harness adapters produce the supported project-scoped layout for their pinned versions, keeping original package provenance. Do not install into global user directories by default. Validate actual harness discovery behavior in integration tests rather than assuming a downloaded directory is automatically recognized.

## Consumers and startup behavior

| Consumer | Skill access |
|---|---|
| Context service | Reads Git files and serves metadata and immutable packages |
| Genkit coordinator/specialists | Discover/load domain skills through context tools; selected revision recorded per run |
| Coding executor | Resolves selected skills when creating a job; stores its lock as a job input |
| Disposable runner | Runs frozen pull during bootstrap, before launching Codex/Claude |
| Local developer | Runs targeted or all pull, optionally selecting a harness target |
| Automation | Declares skill names; resolves them when creating each workflow run |
| CLI/offline development | Uses installed packages and a verified lock/cache |

An automation definition may request a catalog revision or use the current canonical revision at run
creation. Every run freezes its own lock. A retry within the same staged job reuses its packages; a
new job may use a verified cache for that lock, otherwise an unavailable old revision fails rather
than substituting current content. Later jobs without a lock resolve the then-current catalog.
Automations invoke the shared Python resolution service; their runners use the same pull command.

Only one component needs to download packages for each coding job: either the executor stages a verified bundle or the runner pulls its locked packages at startup. Prefer runner bootstrap when it has context-service access; use executor staging in network-restricted environments. Required skills must be installed successfully before coding starts. The runner bootstrap selects the harness target or invokes the corresponding installer before harness launch.

For EKS, this bootstrap can run in the runner entrypoint or an init container with a shared workspace volume. It receives scoped service credentials and the immutable job lock. No dynamic edits to the deployment chart are needed for skill selection.

## Required context-service endpoints

| Endpoint | Purpose |
|---|---|
| `GET /v1/skills` | List domain skill metadata for an admitted caller |
| `GET /v1/skills/{name}` | Load skill instructions at the current revision, optionally requiring that exact revision |
| `POST /v1/skills:resolve` | Resolve named/all selection to an immutable package manifest |
| `GET /v1/skill-packages/{package_id}` | Download the exact domain package |

Immutable package IDs do not bypass project/domain admission. The service retains no historical-package
guarantee. If a required revision is unavailable from the current service and verified cache, report
failure instead of substituting newer content.

## Implementation changes

1. Add a `SkillCatalog` interface and a Git/filesystem implementation that scans validated packages into an in-memory metadata index.
2. Add authenticated resolution and immutable package-download endpoints to the context service.
3. Add Python `SkillClient`, `SkillInstaller`, and versioned lock/manifest contracts.
4. Implement `team-agent skills list` and `team-agent skills pull`, including selection, destination, revision, target, frozen, non-interactive, and JSON-output options.
5. Add executor resolution and runner bootstrap installation before harness launch. Persist the skill lock in the coding job's inputs.
6. Add named skill selections and optional required exact catalog revisions to automation definitions.
7. Add project-scoped Codex and Claude installation adapters; verify actual harness discovery for pinned versions.
8. Remove any mandatory MongoDB skill-catalog collection or synchronization requirement. Keep run records of skill identities, revisions, and hashes for provenance.

Do not require Genkit-specific types in these interfaces. The same skill distribution services must work for local developers without the hosted coordinator.

## Acceptance criteria

- A targeted pull installs only selected self-contained packages.
- An all pull returns the complete catalog for an admitted caller's project/domain.
- Full packages include referenced resources and scripts; installation executes no scripts.
- Interrupted or invalid downloads do not replace a valid installed package.
- Hash mismatch, unsafe paths, unavailable revisions, and unmanaged-file collisions fail clearly.
- Frozen pulls reproduce exact locked packages from the current service or a verified cache and never resolve latest.
- A retry uses its staged packages or a verified cache; an unavailable frozen package fails without fallback.
- Required skill installation failure prevents coding-harness startup.
- Project-scoped Codex and Claude adapters pass discovery fixtures and live integration checks when the harness is available.
- Genkit tools, the CLI, and runner bootstrap enforce the same project/domain admission boundary.
- No MongoDB connection is needed to discover or download skills from the context service's Git-backed catalog.

# Skill distribution

`team_agent_skills.SkillClient` and `SkillInstaller` are the shared application boundary for local
CLI pulls and future coding-job bootstrap. They consume the context service's authenticated `/v1`
skill APIs; they do not access Git or MongoDB directly and do not depend on Genkit.

## Selection and locking

Each pull uses exactly one selection mode: one or more positional names, `--all`, or `--lock`.
Named and all pulls resolve the current authorized catalog once, optionally requiring `--revision`,
then create a schema-version `"1"` `skills.lock.json`. A lock records the project, generic target,
catalog revision, immutable package IDs, source revisions, complete file inventory, hashes, sizes,
resources, and citations. Pydantic rejects extra fields and unsupported schema versions. Tokens and
service URLs are never serialized.

`--lock --frozen` skips resolution. The project is derived from the validated lock; when an explicit
`--project` is also supplied, it must match. Each exact package is loaded from a verified local cache
when possible, or requested by its immutable ID from the current context service. If neither source
can supply bytes matching the lock, the pull fails; it never asks for or substitutes the latest
package.

## Filesystem behavior

The generic target installs each package under `<dest>/<skill-name>`. Before installation the client
bounds response and metadata sizes, rejects unsafe or colliding relative paths, strictly decodes
base64, independently derives the S04 canonical package ID, checks response metadata against the
manifest, and checks every declared byte length and SHA-256 hash. Resources must be unique, safe, and
declared files. All selected packages are staged before mutation, and each package directory is
replaced with a same-filesystem rename. Package scripts are data and are never run.

`<dest>/.team-agent-ownership.json` records every path, size, and hash managed by the installer. A
pull may replace a package directory only when its complete current inventory still matches that
record; edits, unexpected files, symlinks, and unmanaged collisions fail without removing them.
Targeted pulls leave unselected managed packages and every unrelated file untouched. A pre-existing
lock without a valid ownership manifest, or an ownership manifest without its lock, is treated as
unmanaged. `<dest>/skills.lock.json` describes the latest requested selection, while the ownership
manifest can contain a larger set of packages retained from earlier targeted pulls.

Package replacements, the lock, and ownership metadata commit under one recoverable transaction
journal. Existing packages and metadata remain backed up until the new metadata is durable. An
ordinary failure rolls back immediately; after process or host interruption, the next pull validates
the transaction contents and completes rollback before doing new filesystem work. Recovery refuses
to delete a package directory that no longer matches the journal, protecting files added after an
interruption.

The default cache is the sibling `<dest-parent>/cache`, keeping project pulls local. Both cached
bundles and lock inputs are revalidated before use. The CLI takes credentials only from
`TEAM_AGENT_TOKEN`; `TEAM_AGENT_CONTEXT_URL` selects the service endpoint.

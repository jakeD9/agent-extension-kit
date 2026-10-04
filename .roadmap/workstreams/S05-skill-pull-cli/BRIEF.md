# S05 — Verified Skill Pull and Lockfile CLI

Depends on S04. Add the shared Python `SkillClient` and `SkillInstaller`, versioned
`skills.lock.json`, verified local cache, ownership manifest, and `team-agent skills list/pull`
commands. Support exactly one of names, `--all`, or `--lock`, plus project, destination, revision,
generic target, frozen, non-interactive, and JSON-output options. Download complete self-contained
packages to a temporary directory, validate hashes and safe paths, execute no scripts, and install
atomically while preserving unmanaged files and unselected packages. Frozen pulls use only their
lock and never resolve latest; they may use a verified cache, otherwise unavailable exact packages
fail. All JSON and lock fields use snake_case. Exclude Codex/Claude layouts, MCP, Genkit, and coding
jobs. Verify targeted/all/frozen pulls, cache use, interruption safety, collision and mismatch
failures, actionable nonzero errors, and credential-free locks.

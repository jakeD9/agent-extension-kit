# S08 — Domain Authority and Shared Working Memory Correction

Depends on S06. Completed by the S08 correction. It aligned all delivered context behavior
with the normative domain-authority dogma: one deployment serves one small trusted team/domain;
project/domain admission grants every developer use of the complete knowledge, skill, and shared
working-memory corpus. Remove per-artifact `access_groups`, content `approver` roles, proposals, and
approve/reject promotion from contracts, application policy, transports, persistence, fixtures, and
tests.

Git at one protected, pinned revision is the sole canonical knowledge and skill source. MongoDB
knowledge records are rebuildable projections. MongoDB working memory is immediately team-visible,
fixed as `canonicality: supplemental`, and cannot override conflicting Git. Every admitted developer
can create, search, update with optimistic concurrency, atomically create a superseding record, and
expire memory. Preserve project/domain, provenance, evidence, author, revision, timestamps,
expiration, supersession, idempotency, and append-only audit.

Expose create/search through REST, CLI, and MCP. Keep update, expire, and audit on human/operator
REST and CLI surfaces; models cannot convert memory into canonical knowledge. Verify non-admitted
denial, full admitted-team visibility, no policy fields in wire/storage schemas, Git-over-memory
precedence, lifecycle/idempotency/audit, restart persistence, transport parity, and strict MongoDB
validators. Reverify S01–S06 knowledge/skill behavior after removing group filtering. Exclude
conversation/session state and external-action approval changes.

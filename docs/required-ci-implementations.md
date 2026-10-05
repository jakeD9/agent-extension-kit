# Required CI Implementation: Publish Git Knowledge to MongoDB

## Purpose

This document is the platform-neutral contract for publishing canonical Git knowledge as a
searchable MongoDB projection. A team adopting the kit should implement this contract in GitHub
Actions, GitLab CI, or another protected CI system. It intentionally does not prescribe provider
YAML.

The implementer should be able to build a merge-triggered publisher and a periodic reconciler that
produce the same result for the same commit. The architecture's authority rules are defined in
[Domain Knowledge Authority](domain-knowledge-authority.md).

## Required inputs

The trusted CI event and protected configuration provide:

- repository identity and clone URL;
- exact merged commit SHA, never a moving branch name;
- protected target branch identity;
- project/domain identifier;
- publisher version and schema version;
- context-service publication endpoint or a narrowly scoped database/publisher credential; and
- a stable correlation ID for logs and audit.

The checked-out repository provides the content manifest, knowledge roots, skill declarations, and
referenced resources. They are reviewed inputs, not trusted control-plane values: the publisher must
validate them before use, and they cannot override the event SHA, project/domain, target branch,
publisher schema, or credential scope.

The job must verify that the SHA is reachable from the protected branch before activation. Pull or
merge request jobs may validate content, but only a protected-branch merge job may publish an active
canonical revision.

This kit defines the publication contract and storage invariants, not a ready-made GitHub or GitLab
workflow or a public publisher endpoint. An adopter may wrap the context publication boundary in an
organization-owned CLI/API or use a narrowly scoped direct publisher. Either implementation must
satisfy every requirement below; S24 documents how a team maps the contract onto its CI platform.

## Publication contract

### 1. Trigger and checkout

Run after a successful merge or protected-branch update. Check out the exact event SHA and record the
repository, branch, and SHA. Never index the runner's ambient working tree or resolve `HEAD` later in
the job.

### 2. Validate before publication

Validate the extension manifest, allowed knowledge roots, document schemas, skill packages,
referenced resources, safe paths, bounded file sizes, and citation metadata. Reject symlinks or
content outside the declared checkout boundary. Validation failure must leave the current active
revision unchanged.

### 3. Build deterministically

Normalize, chunk, and enrich content using pinned tool and schema versions. Sort inputs explicitly
and derive stable identifiers from repository identity, path, exact SHA, and chunk identity. If the
deployment generates embeddings, pin the embedding model/configuration and record it in the build
manifest. The same inputs and versions must produce the same logical projection.

Every projected record must retain enough provenance to cite the canonical source: project/domain,
repository, path, exact commit SHA, document/chunk identity, and projection schema/build version.

### 4. Stage idempotently

Write the candidate projection under an immutable revision/build identity with a deterministic
projection hash. Re-running the same SHA
and publisher configuration must reuse or replace only that candidate, without duplicating chunks.
Do not modify the active revision in place and do not expose a partially written candidate to
search.

### 5. Verify completeness

Before activation, compare the staged projection with its build manifest. At minimum verify document
and chunk counts, expected source paths, content hashes, schema compatibility, and required indexes.
Run a bounded smoke query against the candidate when the search backend permits it.

### 6. Activate atomically

Move one project/domain active-revision pointer from the prior complete revision to the verified
candidate using a transaction or compare-and-set operation. Readers must observe either the old
complete projection or the new complete projection, never a mixture. Retain the prior pointer long
enough for immediate rollback.

### 7. Prevent stale publication

Serialize activation per project/domain or use a monotonic generation plus compare-and-set. Before
activation, re-check protected-branch reachability and whether a newer eligible commit has already
won. A slow older job must not replace a newer active revision. Concurrent duplicate jobs for the
same SHA must converge on one visible result.

### 8. Report and clean up

Emit the input SHA, build identity, previous and resulting active revisions, counts, timings,
publisher/schema versions, and terminal status. Clean up abandoned staging revisions according to a
documented retention policy. Cleanup must never delete the active or rollback revision.

## Credentials and trust

Use a short-lived CI identity with permission to stage, verify, activate, and inspect only the
configured project/domain projection. It must not have Git write, merge, production-deploy, or
`agent_runtime` database permissions. Keep credentials in the CI platform's protected secret store,
mask them in logs, and prevent untrusted pull/merge request jobs from receiving them.

Prefer an authenticated context-service publication API so database details remain private. A
direct MongoDB publisher is acceptable only when it implements the same validation, atomicity,
audit, and least-privilege boundaries.

## Failure, rollback, and recovery

- A failure before activation records the failed candidate and leaves the previous revision active.
- A timeout or lost acknowledgement during activation is an uncertain outcome. Preserve an explicit
  uncertain state and read the active pointer before retrying; never mark the candidate failed while
  it may already be active.
- Retry uses the same build identity and is idempotent.
- Rollback atomically selects a previously verified projection; it does not rebuild or edit records
  in place.
- A projection can be rebuilt from Git after total MongoDB loss. Shared working memory is stored
  separately and is not part of this rebuild.
- Do not claim exactly-once execution. Provide one effective active result through idempotency,
  compare-and-set activation, and reconciliation.

## Required reconciliation

In addition to merge-triggered publication, run a scheduled or manually invokable reconciler. It
compares the protected branch's expected commit with the active Mongo revision, checks projection
completeness, and starts or resumes the same publication contract when they differ. It must not use a
different build path from CI and must not activate an unverified revision.

Alert on a prolonged mismatch, repeated failure, an active revision no longer reachable from the
protected branch, missing provenance, or a failed rollback. The reconciler repairs missed events; it
does not make repository changes.

## Acceptance checks

An adoption is complete only when automated tests demonstrate:

1. a protected-branch merge publishes the exact merged SHA;
2. pull/merge request validation cannot activate a revision;
3. identical reruns create no duplicate visible records;
4. interrupted staging leaves the previous revision searchable;
5. readers never observe a mixed revision during activation;
6. a slower older job cannot replace a newer revision;
7. an uncertain activation is reconciled without duplicate publication;
8. rollback restores the previous verified projection atomically;
9. deletion of projected knowledge can be recovered from Git;
10. every search result cites repository, path, and exact SHA;
11. publisher credentials cannot write Git, runtime state, or another domain; and
12. Git/active-revision drift is detected and repaired by reconciliation.

Provider-specific workflow files may add caching, environments, approvals, and status reporting,
but they may not weaken this contract.

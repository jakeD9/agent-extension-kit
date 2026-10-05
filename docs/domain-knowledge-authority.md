# Domain Knowledge Authority

## Architectural dogma

This kit is deployed for one small, trusted engineering team working within one defined domain. The
project/domain admission decision is the content-access boundary. After admission, every developer
may use the complete agent surface, retrieve the team's knowledge and skills, and contribute shared
working memory. Do not add per-memory readers, field-level grants, policy groups, or an application
`approver` role to subdivide that trusted team.

The protected, pinned Git revision is the sole canonical source of reviewed team knowledge and
skills. A change becomes canonical only after the team's normal branch, commit, pull/merge request,
review, and protected-branch controls accept it. The agent, MCP server, context service, CLI,
database, search index, and disposable coding runners consume or propose changes to that content;
none of them can confer canonical status.

These rules are normative. A deployment that needs mutually distrusting audiences should use
separate project/domain deployments or define a new security architecture rather than adding
record-level policy to this one.

## Three different kinds of state

| State | System of record | Meaning |
|---|---|---|
| Canonical knowledge and skills | Protected Git revision | Reviewed instructions and facts the team has accepted |
| Knowledge search representation | MongoDB | Rebuildable projection of one exact Git revision |
| Shared working memory | MongoDB | Team-wide, provenance-bearing observations that have not become canonical |

The precedence rule is:

```text
canonical Git knowledge > shared Mongo working memory > model inference
```

When working memory conflicts with canonical Git content, consumers must follow Git and surface the
conflict. They must not silently blend the records or rank working memory above Git. Working memory
can carry author, provenance, evidence, timestamps, revision, expiration, and supersession data.
Those fields support traceability and lifecycle management; they do not grant authority.

MongoDB may hold normalized chunks, embeddings, search metadata, publication state, and an active
revision pointer for canonical Git content. Those records are disposable projections. Operators
must be able to delete and recreate them from the pinned Git revision without losing canonical
information. Direct database edits never update Git and never become authoritative.

## Contribution paths

Every admitted developer can:

- retrieve all knowledge and skills in the deployment's domain;
- create, search, revise, supersede, and expire shared working memory;
- ask an agent or disposable runner to prepare a Git knowledge or skill change; and
- submit that change through the repository's normal review flow.

A useful working-memory item becomes canonical only when represented as a Git change and merged into
the protected branch. The next successful index publication then makes that exact merged revision
searchable. Repository ownership and review rules determine who may merge; the context service does
not recreate that governance system.

Transport does not alter authority. The REST API, MCP tools, CLI, hosted Genkit runtime, and local or
disposable coding runners must expose the same distinction between canonical content and
supplemental working memory. A model's confidence, repeated use, or successful job cannot promote a
working-memory record.

## What this dogma does not authorize

Trusting a developer with domain content does not authorize every external side effect. Existing
capability controls remain required for branch publication, pull/merge request creation, review
publication, merge, deployment, production mutation, secrets, and data migration. An explicit
developer request or configured automation authorizes only its bounded action and target, using the
minimum required credentials. This does not imply stored approval objects or a second internal
approval workflow, and it is not a content-access control.

Repository content is also untrusted input to the model. Canonical status establishes review and
precedence, not permission to invoke tools or bypass supervisor policy.

## Deployment test

A conforming deployment can answer yes to all of these questions:

1. Is one admitted team able to use and contribute across the complete domain corpus?
2. Can every canonical answer cite an exact repository path and commit SHA?
3. Can the Mongo knowledge projection be rebuilt solely from Git?
4. Is working memory clearly labeled supplemental and unable to override Git?
5. Do knowledge changes become canonical only through protected Git review?
6. Are external side effects still independently authorized and audited?

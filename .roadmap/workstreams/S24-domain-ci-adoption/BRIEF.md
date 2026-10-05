# S24 — Domain Adoption and CI Publication Guide

Depends on S20 and may proceed in parallel with S23. Produce a cold-reader adoption walkthrough for
a specific trusted team/domain using the normative domain-authority dogma and the platform-neutral
required-CI contract. Show how an
operator identifies the protected Git source, project/domain admission boundary, knowledge roots,
skill catalog, context-service deployment, Mongo projection credentials, and configured publication
permissions without adding provider-specific CI YAML to the kit.

The walkthrough must cover both GitHub and GitLab concept mapping while keeping one implementation
contract: merge-triggered exact-SHA validation/build/staging/atomic activation plus scheduled
reconciliation. Include a worked fictional domain, configuration checklist, operational runbook,
rollback exercise, and acceptance-evidence template. Make clear that Mongo knowledge is rebuildable,
Mongo working memory is supplemental and team-wide, and repository review is the only route to
canonical knowledge or skill changes.

Exclude production credentials, organization-specific repositories, hosted infrastructure
manifests, provider workflow YAML, a kit-owned publisher service, and a second indexing
implementation. Document the choice between an organization-owned wrapper around the context
publication boundary and a narrowly scoped direct publisher; do not imply that the kit ships either
integration. Verify the instructions through a fresh-reader
tabletop exercise that traces merge, failed publication, stale concurrent job, rollback, Mongo loss,
and a working-memory-to-Git contribution.

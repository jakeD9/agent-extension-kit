# S20 — Git Publication Adapter

Depends on S15 and S16. This slice publishes verified Git artifacts; it does not govern access to
domain content or promote working memory. An explicit developer request or configured automation
authorizes a bounded branch, draft-PR, or review publication for its repository and exact revision.
Do not add internal approval objects, policy fingerprints, or expiry machinery.

Add one provider-neutral Git publication contract and one configured provider adapter using
supervisor-owned credentials. Persist an idempotency key and enough remote identity to reconcile a
timeout or ambiguous response before retrying. Revalidate the exact revision and independent checks
at publication time. Never expose publication credentials to a model harness or verification
environment. Merge, deploy, production mutation, and data migration remain outside v1. Verify
idempotency, ambiguous-result reconciliation, stale-revision rejection, least privilege, credential
separation, and opt-in real-provider smoke tests.

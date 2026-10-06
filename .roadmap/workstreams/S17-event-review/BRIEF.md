# S17 — Event-Driven Review Projection

Depends on S14 and S16. Deterministically parse and allowlist merge-request URLs; add event
automation definitions, consolidated mock publication, and Slack reaction projection. At the S17
technical check-in, ground idempotency in duplicate-message and same-head workflows before choosing
whether separate review and projection-event keys are actually required; do not add both by default.
Definitions select named skills and may require a catalog revision;
run creation resolves one immutable skill lock. Same-job retries reuse staged packages; a new job
uses a verified cache or fails when the exact package is unavailable after later deployments. Persist
publication before projection and treat emoji as non-authoritative. Review identity uses repository,
merge request, exact head SHA, and workflow version; project admission remains the content boundary.
Verify duplicate messages/events, locked-skill retry behavior, one review per exact SHA,
ambiguous publication recovery, new heads, and repairable projection state.

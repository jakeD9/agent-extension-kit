# S17 — Event-Driven Review Projection

Depends on S14 and S16. Deterministically parse and allowlist merge-request URLs; add event
automation definitions, message-specific and canonical review keys, consolidated mock publication,
and Slack reaction projection. Definitions select named skills and may require a catalog revision;
run creation resolves one immutable skill lock. Same-job retries reuse staged packages; a new job
uses a verified cache or fails when the exact package is unavailable after later deployments. Persist
publication before projection and treat emoji as non-authoritative. Verify duplicate messages/events,
access-scope and policy fingerprints, locked-skill retry behavior, one review per exact SHA,
ambiguous publication recovery, new heads, and repairable projection state.

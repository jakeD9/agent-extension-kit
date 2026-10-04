# S15 — Event-Driven Review Projection

Depends on S12 and S14. Deterministically parse and allowlist merge-request URLs; add event
automation definitions, message-specific and canonical review keys, consolidated mock publication,
and Slack reaction projection. Persist publication before projection and treat emoji as non-
authoritative. Verify duplicate messages/events, access-scope and policy fingerprints, one review
per exact SHA, ambiguous publication recovery, new heads, and repairable projection state.

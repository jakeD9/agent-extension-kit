# S03 — Authorized Skill-Catalog Tracer

Depends on S02. Port skill metadata/body parsing into Python, persist revision-pinned skill records,
and expose authorized REST list/get operations through the same principal and policy boundary as
knowledge search. Metadata discovery must not leak unauthorized skill names, and loading a body must
preserve repository/path/revision provenance. Exclude CLI, MCP, memory, and Genkit. Verify manifest
validation, synchronization/pruning, authorization, citations, restart persistence, and canonical
REST errors.

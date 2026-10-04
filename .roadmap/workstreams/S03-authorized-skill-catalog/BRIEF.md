# S03 — Authorized Skill-Catalog Tracer

Depends on S02. Port skill metadata/body parsing into Python, scan the pinned Git/filesystem content
pack into an in-memory catalog at startup, and expose authorized REST list/get operations through the
same principal and policy boundary as knowledge search. Metadata discovery must not leak unauthorized
skill names, and loading a body must preserve repository/path/revision provenance without requiring a
MongoDB skill collection. Exclude package resolution/download, CLI, MCP, memory, and Genkit. Verify
manifest validation, authorization, citations, restart rebuild behavior, and canonical REST errors.

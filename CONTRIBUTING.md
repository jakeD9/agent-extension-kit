# Contributing

Use Python 3.12 and the uv lockfile. Keep changes inside one roadmap workstream and begin a fresh
session by reading the canonical architecture brief, workstream status, the slice brief, and its
dependency summaries.

Before submitting a change, run:

```sh
uv lock --check
uv run ruff check .
uv run ruff format --check .
uv run mypy
uv run pytest
uv build --all-packages
```

Only claim live provider or external-service behavior when the corresponding opt-in test actually
ran. New skills need positive and negative trigger cases plus an end-to-end fixture. Provider and
harness integrations implement shared ports and pass contract suites rather than adding provider
conditionals to core services.

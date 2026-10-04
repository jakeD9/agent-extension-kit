from pathlib import Path

import pytest
from team_context_service.main import create_app_from_env

EXTENSION_PATH = Path(__file__).parents[3] / "extension"


def test_context_service_rejects_runtime_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_PATH", str(EXTENSION_PATH))
    monkeypatch.setenv("TEAM_CONTEXT_MONGODB_URI", "mongodb://context.invalid")
    monkeypatch.setenv("TEAM_CONTEXT_MONGODB_DATABASE", "agent_runtime")
    monkeypatch.setenv("AGENT_RUNTIME_MONGODB_DATABASE", "agent_runtime")

    with pytest.raises(ValueError, match="must differ"):
        create_app_from_env()

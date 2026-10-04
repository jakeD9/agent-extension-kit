import json
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[3]


def test_codex_mcp_configuration_is_project_scoped_and_credential_free() -> None:
    path = ROOT / ".codex/config.toml"
    config = tomllib.loads(path.read_text())

    assert config["mcp_servers"]["team_context"] == {
        "command": "uv",
        "args": ["run", "team-context-mcp"],
    }
    assert "TEAM_AGENT_TOKEN" not in path.read_text()


def test_claude_mcp_configuration_forwards_environment_names_not_secrets() -> None:
    path = ROOT / ".mcp.json"
    config = json.loads(path.read_text())
    server = config["mcpServers"]["team-context"]

    assert server["command"] == "uv"
    assert server["args"] == ["run", "team-context-mcp"]
    assert server["env"] == {
        "TEAM_AGENT_CONTEXT_URL": "${TEAM_AGENT_CONTEXT_URL}",
        "TEAM_AGENT_TOKEN": "${TEAM_AGENT_TOKEN}",
    }
    assert "dev-token" not in path.read_text()

import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from team_agent_runtime.executor_worker import ExecutorSettings


def test_executor_settings_require_explicit_stable_trusted_configuration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repository = tmp_path / "repository"
    repository.mkdir()
    monkeypatch.setenv("AGENT_RUNTIME_MONGODB_URI", "mongodb://runtime.example/agent_runtime")
    monkeypatch.setenv("TEAM_AGENT_CONTEXT_URL", "https://context.example")
    monkeypatch.setenv("TEAM_AGENT_TOKEN", "scoped-token")
    monkeypatch.setenv("TEAM_AGENT_EXECUTOR_WORKER_ID", "executor-a")
    monkeypatch.setenv("TEAM_AGENT_CODING_RUNNER_IMAGE", "company/coding-runner:pinned")
    monkeypatch.setenv("TEAM_AGENT_CODING_ARTIFACT_ROOT", str(tmp_path / "artifacts"))
    monkeypatch.setenv(
        "TEAM_AGENT_EXECUTOR_REPOSITORIES",
        json.dumps({"company/service": str(repository)}),
    )

    settings = ExecutorSettings.from_env()

    assert settings.worker_id == "executor-a"
    assert settings.repositories == {"company/service": repository}
    assert settings.codex_home is None


def test_executor_settings_reject_relative_paths_and_half_configured_live_auth(
    tmp_path: Path,
) -> None:
    base = {
        "mongodb_uri": "mongodb://runtime.example/agent_runtime",
        "context_url": "https://context.example",
        "context_token": "token",
        "worker_id": "executor-a",
        "image": "company/coding-runner:pinned",
        "artifact_root": tmp_path / "artifacts",
        "repositories": {"company/service": tmp_path / "repo"},
    }
    with pytest.raises(ValidationError, match="absolute"):
        ExecutorSettings.model_validate({**base, "artifact_root": "relative"})
    with pytest.raises(ValidationError, match="configured together"):
        ExecutorSettings.model_validate({**base, "codex_home": tmp_path / "codex"})

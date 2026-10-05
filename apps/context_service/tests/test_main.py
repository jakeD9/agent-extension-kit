from pathlib import Path

import pytest
from fastapi import FastAPI
from team_context_core import GitSkillCatalog
from team_context_service import main as context_main
from team_context_service.app import AppDependencies
from team_context_service.main import create_app_from_env

EXTENSION_PATH = Path(__file__).parents[3] / "extension"


def test_context_service_rejects_runtime_database_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_PATH", str(EXTENSION_PATH))
    monkeypatch.setenv("CONTENT_REVISION", "a" * 40)
    monkeypatch.setenv("TEAM_CONTEXT_MONGODB_URI", "mongodb://context.invalid")
    monkeypatch.setenv("TEAM_CONTEXT_MONGODB_DATABASE", "agent_runtime")
    monkeypatch.setenv("AGENT_RUNTIME_MONGODB_DATABASE", "agent_runtime")

    with pytest.raises(ValueError, match="must differ"):
        create_app_from_env()


def test_context_service_builds_skill_catalog_from_git_pack(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, AppDependencies] = {}

    def capture_dependencies(dependencies: AppDependencies) -> FastAPI:
        captured["dependencies"] = dependencies
        return FastAPI()

    monkeypatch.setenv("CONTENT_PATH", str(EXTENSION_PATH))
    monkeypatch.setenv("CONTENT_REVISION", "a" * 40)
    monkeypatch.setenv("TEAM_CONTEXT_MONGODB_URI", "mongodb://context.invalid")
    monkeypatch.setattr(context_main, "build_app", capture_dependencies)

    create_app_from_env()

    dependencies = captured["dependencies"]
    assert isinstance(dependencies.skill_catalog, GitSkillCatalog)


def test_context_service_requires_immutable_revision_without_explicit_dev_override(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("CONTENT_PATH", str(EXTENSION_PATH))
    monkeypatch.setenv("CONTENT_REVISION", "working-tree")
    monkeypatch.setenv("TEAM_CONTEXT_MONGODB_URI", "mongodb://context.invalid")

    with pytest.raises(ValueError, match="immutable"):
        create_app_from_env()

    monkeypatch.setenv("TEAM_AGENT_ALLOW_MUTABLE_CONTENT_REVISION", "true")
    app = create_app_from_env()
    assert isinstance(app, FastAPI)

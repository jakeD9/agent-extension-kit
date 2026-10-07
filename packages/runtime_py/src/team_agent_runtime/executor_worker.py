"""Trusted persistent worker entry point for supervised local Docker execution."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, model_validator
from pymongo import AsyncMongoClient
from pymongo.server_api import ServerApi
from team_agent_skills import SkillClient

from team_agent_runtime.docker_executor import (
    FrozenCodexSkillStager,
    LocalDockerExecutor,
    LocalDockerRunner,
    LocalRepositoryCatalog,
    SubprocessCommandAdapter,
)
from team_agent_runtime.mongo_jobs import MongoCodingJobRepository


class ExecutorSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mongodb_uri: str = Field(min_length=1)
    mongodb_database: str = Field(default="agent_runtime", min_length=1)
    context_url: str = Field(min_length=1)
    context_token: str = Field(min_length=1)
    worker_id: str = Field(min_length=1, max_length=128)
    image: str = Field(min_length=1)
    artifact_root: Path
    repositories: dict[str, Path] = Field(min_length=1, max_length=100)
    poll_seconds: float = Field(default=1.0, ge=0.05, le=60)
    codex_home: Path | None = None
    model_network: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def trusted_paths_are_explicit(self) -> ExecutorSettings:
        paths = [self.artifact_root, *self.repositories.values()]
        if self.codex_home is not None:
            paths.append(self.codex_home)
        if any(not path.is_absolute() for path in paths):
            raise ValueError("executor filesystem paths must be absolute")
        if (self.codex_home is None) != (self.model_network is None):
            raise ValueError("Codex home and model network must be configured together")
        return self

    @classmethod
    def from_env(cls) -> ExecutorSettings:
        try:
            repositories = json.loads(os.environ.get("TEAM_AGENT_EXECUTOR_REPOSITORIES", ""))
        except json.JSONDecodeError as error:
            raise ValueError("TEAM_AGENT_EXECUTOR_REPOSITORIES must be a JSON object") from error
        if not isinstance(repositories, dict) or any(
            not isinstance(key, str) or not isinstance(value, str)
            for key, value in repositories.items()
        ):
            raise ValueError("TEAM_AGENT_EXECUTOR_REPOSITORIES must map identifiers to paths")
        return cls(
            mongodb_uri=os.environ.get("AGENT_RUNTIME_MONGODB_URI", ""),
            mongodb_database=os.environ.get("AGENT_RUNTIME_MONGODB_DATABASE", "agent_runtime"),
            context_url=os.environ.get("TEAM_AGENT_CONTEXT_URL", ""),
            context_token=os.environ.get("TEAM_AGENT_TOKEN", ""),
            worker_id=os.environ.get("TEAM_AGENT_EXECUTOR_WORKER_ID", ""),
            image=os.environ.get("TEAM_AGENT_CODING_RUNNER_IMAGE", ""),
            artifact_root=Path(os.environ.get("TEAM_AGENT_CODING_ARTIFACT_ROOT", "")),
            repositories={key: Path(value) for key, value in repositories.items()},
            poll_seconds=float(os.environ.get("TEAM_AGENT_EXECUTOR_POLL_SECONDS", "1")),
            codex_home=(
                Path(value) if (value := os.environ.get("TEAM_AGENT_CODEX_HOME")) else None
            ),
            model_network=os.environ.get("TEAM_AGENT_CODEX_MODEL_NETWORK"),
        )


async def run_worker(settings: ExecutorSettings, *, once: bool = False) -> None:
    commands = SubprocessCommandAdapter()
    mongo: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        settings.mongodb_uri,
        serverSelectionTimeoutMS=5_000,
        server_api=ServerApi("1"),
        tz_aware=True,
    )
    skills = SkillClient(settings.context_url, settings.context_token)
    try:
        repository = MongoCodingJobRepository(mongo[settings.mongodb_database])
        await repository.migrate()
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog(settings.repositories, commands),
            FrozenCodexSkillStager(skills),
            artifact_root=settings.artifact_root,
            image=settings.image,
            codex_home=settings.codex_home,
            model_network=settings.model_network,
        )
        await runner.reconcile_orphans(repository)
        executor = LocalDockerExecutor(repository, runner, worker_id=settings.worker_id)
        while True:
            completed = await executor.execute_one()
            if once:
                return
            if completed is None:
                await asyncio.sleep(settings.poll_seconds)
    finally:
        skills.close()
        await mongo.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the trusted Codex job executor")
    parser.add_argument("--once", action="store_true", help="poll at most one job, then exit")
    args = parser.parse_args(argv)
    asyncio.run(run_worker(ExecutorSettings.from_env(), once=args.once))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

import os
import re
from pathlib import Path
from typing import Any, cast

import uvicorn
from fastapi import FastAPI
from pymongo import AsyncMongoClient
from pymongo.server_api import ServerApi
from team_agent_auth import StaticBearerAuthenticator
from team_agent_database import (
    KnowledgeChunkCollection,
    MongoContextRepository,
    MongoKnowledgeIndex,
    MongoSharedMemory,
)
from team_context_core import GitSkillCatalog

from team_context_service.app import AppDependencies, build_app
from team_context_service.content_pack import load_content_pack
from team_context_service.persistence import ContextDatabase, MongoContextBackend


def _enabled(name: str) -> bool:
    value = os.environ.get(name, "false").lower()
    if value not in {"true", "false"}:
        raise ValueError(f"{name} must be true or false")
    return value == "true"


def _content_revision() -> str:
    revision = os.environ.get("CONTENT_REVISION", "")
    if not revision:
        raise ValueError("CONTENT_REVISION is required")
    if re.fullmatch(r"[0-9a-f]{40}", revision) is None and not _enabled(
        "TEAM_AGENT_ALLOW_MUTABLE_CONTENT_REVISION"
    ):
        raise ValueError("CONTENT_REVISION must be an immutable 40-character commit SHA")
    return revision


def create_app_from_env() -> FastAPI:
    content_path = Path(os.environ.get("CONTENT_PATH", "extension"))
    revision = _content_revision()
    principals = os.environ.get("DEV_AUTH_PRINCIPALS", "{}")
    mongo_uri = os.environ.get("TEAM_CONTEXT_MONGODB_URI")
    if not mongo_uri:
        raise ValueError("TEAM_CONTEXT_MONGODB_URI is required")
    database_name = os.environ.get("TEAM_CONTEXT_MONGODB_DATABASE", "team_context")
    runtime_database_name = os.environ.get("AGENT_RUNTIME_MONGODB_DATABASE", "agent_runtime")
    if database_name == runtime_database_name:
        raise ValueError("Context and runtime MongoDB databases must differ")

    pack = load_content_pack(content_path, revision)
    client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
        mongo_uri,
        serverSelectionTimeoutMS=5_000,
        server_api=ServerApi("1"),
        tz_aware=True,
    )
    database = client[database_name]
    repository = MongoContextRepository(database)
    shared_memory = MongoSharedMemory(database)
    backend = MongoContextBackend(
        client,
        cast(ContextDatabase, database),
        repository,
        pack,
        transaction_verifier=shared_memory.verify_transactions,
        bootstrap_content=_enabled("TEAM_AGENT_BOOTSTRAP_CONTENT_ON_STARTUP"),
        allow_mutable_content_revision=_enabled("TEAM_AGENT_ALLOW_MUTABLE_CONTENT_REVISION"),
    )
    knowledge_index = MongoKnowledgeIndex(
        cast(KnowledgeChunkCollection, database["knowledge_chunks"]),
        cast(KnowledgeChunkCollection, database["active_knowledge_revisions"]),
        pack.manifest.id,
    )
    skill_catalog = GitSkillCatalog(pack.skills, pack.revision)
    return build_app(
        AppDependencies(
            pack=pack,
            authenticator=StaticBearerAuthenticator.from_json(principals),
            knowledge_index=knowledge_index,
            skill_catalog=skill_catalog,
            shared_memory=shared_memory,
            readiness=backend.readiness,
            startup=backend.startup,
            shutdown=backend.shutdown,
        )
    )


def run() -> None:
    uvicorn.run(
        create_app_from_env(),
        host=os.environ.get("HTTP_HOST", "0.0.0.0"),
        port=int(os.environ.get("HTTP_PORT", "3000")),
    )


if __name__ == "__main__":
    run()

import asyncio
import os
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure
from pymongo.server_api import ServerApi
from team_agent_contracts import KnowledgeSearchRequest, Principal, SkillListRequest
from team_agent_database import (
    KnowledgeChunkCollection,
    MongoContextRepository,
    MongoKnowledgeIndex,
    MongoSkillCatalog,
    SkillCollection,
)
from team_context_service import load_content_pack

EXTENSION_PATH = Path(__file__).parents[2] / "extension"


def test_context_persists_across_clients_and_cannot_read_runtime_database() -> None:
    uri = os.environ.get("TEST_TEAM_CONTEXT_MONGODB_URI")
    if uri is None:
        pytest.skip("TEST_TEAM_CONTEXT_MONGODB_URI is not configured")
    context_database = os.environ.get("TEAM_CONTEXT_MONGODB_DATABASE", "team_context")
    runtime_database = os.environ.get("AGENT_RUNTIME_MONGODB_DATABASE", "agent_runtime")
    source_id = f"integration-{uuid4()}"

    async def exercise() -> None:
        first: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(uri, server_api=ServerApi("1"))
        database = first[context_database]
        repository = MongoContextRepository(database)
        await repository.migrate()
        pack = load_content_pack(EXTENSION_PATH, "integration-revision")
        chunks = [
            chunk.model_copy(update={"id": f"{source_id}:{chunk.id}", "project": source_id})
            for chunk in pack.chunks
        ]
        skills = [
            skill.model_copy(update={"id": f"{source_id}:{skill.id}", "projects": [source_id]})
            for skill in pack.skills
        ]
        await repository.synchronize(source_id, "integration-revision", chunks, skills)
        await first.close()

        restarted: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri, server_api=ServerApi("1")
        )
        restarted_database = restarted[context_database]
        index = MongoKnowledgeIndex(
            cast(KnowledgeChunkCollection, restarted_database["document_chunks"])
        )
        results = await index.search(
            KnowledgeSearchRequest(query="vendor idempotency identifier", project=source_id),
            Principal(
                id="integration",
                groups=["engineering"],
                projects=[source_id],
            ),
        )
        assert results
        assert results[0].citation.revision == "integration-revision"

        denied = await index.search(
            KnowledgeSearchRequest(query="idempotency", project=source_id),
            Principal(id="outsider", groups=["other"], projects=[source_id]),
        )
        assert denied == []

        skill_catalog = MongoSkillCatalog(
            cast(SkillCollection, restarted_database["skills"]), source_id=source_id
        )
        listed_skills, next_cursor = await skill_catalog.list(
            SkillListRequest(project=source_id),
            Principal(id="integration", groups=["engineering"], projects=[source_id]),
        )
        assert [skill.name for skill in listed_skills] == ["diagnose-and-fix"]
        assert next_cursor is None
        loaded_skill = await skill_catalog.get(
            "diagnose-and-fix",
            source_id,
            Principal(id="integration", groups=["engineering"], projects=[source_id]),
        )
        assert loaded_skill is not None
        assert loaded_skill.body.startswith("# Diagnose and fix")
        assert loaded_skill.citation.revision == "integration-revision"
        unauthorized_skill = await skill_catalog.get(
            "diagnose-and-fix",
            source_id,
            Principal(id="outsider", groups=["other"], projects=[source_id]),
        )
        assert unauthorized_skill is None

        with pytest.raises(OperationFailure, match="not authorized"):
            await restarted[runtime_database]["sessions"].find_one({})

        await restarted_database["documents"].delete_many({"source_id": source_id})
        await restarted_database["document_chunks"].delete_many({"source_id": source_id})
        await restarted_database["skills"].delete_many({"source_id": source_id})
        await restarted_database["source_revisions"].delete_many({"source_id": source_id})
        await restarted.close()

    asyncio.run(exercise())

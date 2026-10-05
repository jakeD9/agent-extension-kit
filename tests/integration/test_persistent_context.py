import asyncio
import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import OperationFailure, WriteError
from pymongo.server_api import ServerApi
from team_agent_contracts import (
    Citation,
    KnowledgeSearchRequest,
    MemoryDecisionRequest,
    MemoryProposalCreateRequest,
    MemorySearchRequest,
    Principal,
    SkillListRequest,
)
from team_agent_database import (
    KnowledgeChunkCollection,
    MongoContextRepository,
    MongoGovernedMemory,
    MongoKnowledgeIndex,
)
from team_context_core import GitSkillCatalog, MemoryConflictError
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
        first: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri, server_api=ServerApi("1"), tz_aware=True
        )
        database = first[context_database]
        repository = MongoContextRepository(database)
        await repository.migrate()
        memory_service = MongoGovernedMemory(database)
        await memory_service.verify_transactions()
        invalid_proposal = {
            "_id": f"invalid-{source_id}",
            "schema_version": "1",
            "project": source_id,
            "access_groups": ["engineering"],
            "title": "Invalid fixture",
            "body": "The database must reject this record.",
            "provenance": {"repository": "org/repo", "path": "INC.md", "revision": "rev"},
            "evidence": [],
            "author_id": "integration",
            "status": "proposed",
            "expires_at": datetime.now(UTC) + timedelta(days=1),
            "created_at": datetime.now(UTC),
            "updated_at": datetime.now(UTC),
            "revision": 1,
        }
        with pytest.raises(WriteError):
            await database["memory_proposals"].insert_one(invalid_proposal)
        invalid_proposal["_id"] = f"invalid-citation-{source_id}"
        invalid_proposal["evidence"] = [
            {"repository": "org/repo", "path": "test.py", "revision": "rev"}
        ]
        invalid_proposal["provenance"] = {
            "repository": "",
            "path": "INC.md",
            "revision": "rev",
        }
        with pytest.raises(WriteError):
            await database["memory_proposals"].insert_one(invalid_proposal)
        pack = load_content_pack(EXTENSION_PATH, "integration-revision")
        chunks = [
            chunk.model_copy(update={"id": f"{source_id}:{chunk.id}", "project": source_id})
            for chunk in pack.chunks
        ]
        skills = [
            skill.model_copy(update={"id": f"{source_id}:{skill.id}", "projects": [source_id]})
            for skill in pack.skills
        ]
        await repository.synchronize(source_id, "integration-revision", chunks)
        await first.close()

        restarted: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri, server_api=ServerApi("1"), tz_aware=True
        )
        restarted_database = restarted[context_database]
        restarted_memory = MongoGovernedMemory(restarted_database)
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

        skill_catalog = GitSkillCatalog(skills, pack.revision)
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

        author = Principal(id=f"author-{source_id}", groups=["engineering"], projects=[source_id])
        approver = Principal(
            id=f"approver-{source_id}",
            groups=["engineering"],
            projects=[source_id],
            roles=["approver"],
        )
        proposal_request = MemoryProposalCreateRequest(
            project=source_id,
            access_groups=["engineering"],
            title="Restart-persistent retry key",
            body="Use the vendor delivery identifier.",
            provenance=Citation(repository="org/incidents", path="INC.md", revision="a"),
            evidence=[Citation(repository="org/service", path="test.py", revision="b")],
            expires_at=datetime.now(UTC) + timedelta(days=1),
        )
        identical_key = f"same-race-{source_id}"
        identical_results = await asyncio.gather(
            restarted_memory.propose(proposal_request, author, identical_key),
            restarted_memory.propose(proposal_request, author, identical_key),
            return_exceptions=True,
        )
        assert all(not isinstance(result, BaseException) for result in identical_results)
        identical_ids = {
            result.id for result in identical_results if not isinstance(result, BaseException)
        }
        assert len(identical_ids) == 1
        assert (
            await restarted_database["audit_events"].count_documents(
                {"idempotency_key": identical_key}
            )
            == 1
        )
        assert (
            await restarted_database["memory_idempotency"].count_documents(
                {"_id": {"$regex": identical_key}}
            )
            == 1
        )

        conflicting_key = f"different-race-{source_id}"
        conflicting_results = await asyncio.gather(
            restarted_memory.propose(
                proposal_request.model_copy(update={"title": "First claim"}),
                author,
                conflicting_key,
            ),
            restarted_memory.propose(
                proposal_request.model_copy(update={"title": "Second claim"}),
                author,
                conflicting_key,
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, MemoryConflictError) for result in conflicting_results) == 1
        assert sum(not isinstance(result, BaseException) for result in conflicting_results) == 1
        assert (
            await restarted_database["audit_events"].count_documents(
                {"idempotency_key": conflicting_key}
            )
            == 1
        )
        assert (
            await restarted_database["memory_idempotency"].count_documents(
                {"_id": {"$regex": conflicting_key}}
            )
            == 1
        )
        proposal = await restarted_memory.propose(proposal_request, author, f"propose-{source_id}")
        approved = await restarted_memory.decide(
            proposal.id,
            MemoryDecisionRequest(expected_revision=1, reason="verified"),
            approver,
            f"approve-{source_id}",
            approve=True,
        )
        replayed = await restarted_memory.decide(
            proposal.id,
            MemoryDecisionRequest(expected_revision=1, reason="verified"),
            approver,
            f"approve-{source_id}",
            approve=True,
        )
        assert replayed.memory == approved.memory
        racing_proposal = await restarted_memory.propose(
            proposal_request,
            author,
            f"race-propose-{source_id}",
        )
        race_results = await asyncio.gather(
            restarted_memory.decide(
                racing_proposal.id,
                MemoryDecisionRequest(expected_revision=1, reason="reviewer one"),
                approver,
                f"race-one-{source_id}",
                approve=True,
            ),
            restarted_memory.decide(
                racing_proposal.id,
                MemoryDecisionRequest(expected_revision=1, reason="reviewer two"),
                approver,
                f"race-two-{source_id}",
                approve=False,
            ),
            return_exceptions=True,
        )
        assert sum(isinstance(result, MemoryConflictError) for result in race_results) == 1
        assert sum(not isinstance(result, BaseException) for result in race_results) == 1
        await restarted.close()

        final_client: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri, server_api=ServerApi("1"), tz_aware=True
        )
        final_database = final_client[context_database]
        final_memory = MongoGovernedMemory(final_database)
        found, _ = await final_memory.search(
            MemorySearchRequest(query="delivery identifier", project=source_id), author
        )
        assert approved.memory is not None
        assert approved.memory.id in [item.id for item in found]

        with pytest.raises(OperationFailure, match="not authorized"):
            await final_client[runtime_database]["sessions"].find_one({})

        await final_database["documents"].delete_many({"source_id": source_id})
        await final_database["document_chunks"].delete_many({"source_id": source_id})
        await final_database["source_revisions"].delete_many({"source_id": source_id})
        await final_database["memories"].delete_many({"project": source_id})
        await final_database["memory_proposals"].delete_many({"project": source_id})
        await final_database["audit_events"].delete_many({"project": source_id})
        await final_database["memory_idempotency"].delete_many({"_id": {"$regex": source_id}})
        await final_client.close()

    asyncio.run(exercise())

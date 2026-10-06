import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient
from pymongo.server_api import ServerApi
from team_agent_contracts import Citation, SkillFileManifest, SkillLock, SkillLockPackage
from team_agent_runtime import (
    AgentDecision,
    AgentTurnRequest,
    AgentTurnResult,
    AgentTurnStatus,
    AgentUsage,
    DecisionKind,
    UsageSource,
)
from team_agent_runtime.conversations import request_fingerprint
from team_agent_runtime.mongo_conversations import MongoConversationRepository


def _request(run_id: str, objective: str) -> AgentTurnRequest:
    return AgentTurnRequest(
        run_id=run_id,
        project="integration",
        objective=objective,
        skill_names=["incident-guide"],
    )


def _result(request: AgentTurnRequest, text: str) -> AgentTurnResult:
    citation = Citation(
        repository="company/runbooks",
        path="worker.md",
        revision="integration-revision",
    )
    package = SkillLockPackage(
        package_id=f"sha256:{'a' * 64}",
        name="incident-guide",
        description="Recover workers.",
        version="1.0.0",
        source_revision="integration-revision",
        files=[SkillFileManifest(path="SKILL.md", sha256="b" * 64, size=10)],
        citation=citation,
    )
    return AgentTurnResult(
        run_id=request.run_id,
        status=AgentTurnStatus.COMPLETED,
        text=text,
        canonical_knowledge_citations=[citation],
        supplemental_memory_citations=[],
        content_revision="integration-revision",
        selected_skill_lock=SkillLock(
            project=request.project,
            catalog_revision="integration-revision",
            packages=[package],
        ),
        decisions=[
            AgentDecision(
                kind=DecisionKind.SOURCE_PRECEDENCE,
                name="canonical_knowledge_over_memory",
                detail="Git is canonical.",
            )
        ],
        usage=AgentUsage(source=UsageSource.SYNTHETIC, total_tokens=1),
        failures=[],
    )


def test_runtime_conversation_transactions_and_restart() -> None:
    uri = os.environ.get("AGENT_RUNTIME_MONGODB_URI")
    if uri is None:
        pytest.skip("AGENT_RUNTIME_MONGODB_URI is not configured")
    database_name = os.environ.get("AGENT_RUNTIME_MONGODB_DATABASE", "agent_runtime")
    conversation_id = f"integration-{uuid4()}"

    async def exercise() -> None:
        first: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri, server_api=ServerApi("1"), tz_aware=True
        )
        first_database = first[database_name]
        repository = MongoConversationRepository(first_database)
        await repository.migrate()
        now = datetime.now(UTC)
        first_request = _request(f"{conversation_id}-run-1", "Recover the worker")
        first_claim = await repository.claim(
            conversation_id,
            first_request,
            request_fingerprint(first_request),
            now,
            now + timedelta(seconds=60),
        )
        await repository.complete(first_claim, _result(first_request, "Worker recovered"), now)
        await first.close()

        restarted: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri, server_api=ServerApi("1"), tz_aware=True
        )
        restarted_database = restarted[database_name]
        restarted_repository = MongoConversationRepository(restarted_database)
        second_request = _request(f"{conversation_id}-run-2", "Did it stay healthy?")
        second_claim = await restarted_repository.claim(
            conversation_id,
            second_request,
            request_fingerprint(second_request),
            now + timedelta(seconds=1),
            now + timedelta(seconds=61),
        )
        assert [(turn.user, turn.assistant) for turn in second_claim.context.recent_turns] == [
            ("Recover the worker", "Worker recovered")
        ]
        second_result = _result(second_request, "The worker stayed healthy")
        await restarted_repository.complete(second_claim, second_result, now + timedelta(seconds=2))
        replay = await restarted_repository.claim(
            conversation_id,
            second_request,
            request_fingerprint(second_request),
            now + timedelta(seconds=3),
            now + timedelta(seconds=63),
        )
        assert replay.replay_result == second_result

        await restarted_database["conversation_turns"].delete_many(
            {"conversation_id": conversation_id}
        )
        await restarted_database["conversations"].delete_one({"_id": conversation_id})
        await restarted.close()

    asyncio.run(exercise())

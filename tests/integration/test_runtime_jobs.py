import asyncio
import os
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from pymongo import AsyncMongoClient
from pymongo.errors import WriteError
from pymongo.server_api import ServerApi
from team_agent_contracts import Citation, SkillFileManifest, SkillLock, SkillLockPackage
from team_agent_runtime import (
    CodingJobConflictError,
    CodingJobOutcome,
    CodingJobRequest,
    CodingJobResult,
    CodingJobStatus,
    MockPublicationOutcome,
    RunStatus,
    StaleCodingJobClaimError,
    coding_job_completion_fingerprint,
)
from team_agent_runtime.mongo_jobs import MongoCodingJobRepository


def _request(identity: str, *, deadline_at: datetime) -> CodingJobRequest:
    revision = "integration-content-revision"
    citation = Citation(
        repository="company/agent-extension",
        path="extension/skills/diagnose-and-fix/SKILL.md",
        revision=revision,
    )
    skill_lock = SkillLock(
        project="integration",
        target="generic",
        catalog_revision=revision,
        packages=[
            SkillLockPackage(
                package_id=f"sha256:{'a' * 64}",
                name="diagnose-and-fix",
                description="Diagnose and fix.",
                version="1.0.0",
                source_revision=revision,
                files=[SkillFileManifest(path="SKILL.md", sha256="b" * 64, size=10)],
                citation=citation,
            )
        ],
    )
    return CodingJobRequest(
        job_id=f"{identity}-job",
        run_id=f"{identity}-run",
        conversation_id=f"{identity}-conversation",
        source_turn_id=f"{identity}-turn",
        submission_idempotency_key=f"{identity}-submission",
        project="integration",
        repository="company/service",
        repository_revision="commit-123",
        objective="Repair the worker",
        content_revision=revision,
        selected_skill_lock=skill_lock,
        deadline_at=deadline_at,
    )


def test_runtime_job_transactions_fencing_and_restart() -> None:
    uri = os.environ.get("AGENT_RUNTIME_MONGODB_URI")
    if uri is None:
        pytest.skip("AGENT_RUNTIME_MONGODB_URI is not configured")
    database_name = os.environ.get("AGENT_RUNTIME_MONGODB_DATABASE", "agent_runtime")
    identity = f"integration-job-{uuid4()}"

    async def exercise() -> None:
        now = datetime.now(UTC)
        request = _request(identity, deadline_at=now + timedelta(minutes=5))
        first: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri, server_api=ServerApi("1"), tz_aware=True
        )
        database = first[database_name]
        repository = MongoCodingJobRepository(database)
        await repository.migrate(now)
        # Failed prior opt-in runs may leave only this test's namespaced fixtures queued.
        await database["coding_jobs"].delete_many({"_id": {"$regex": "^integration-job-"}})
        await database["runs"].delete_many({"_id": {"$regex": "^integration-job-"}})
        submitted = await repository.submit(request, now)
        replay = await repository.submit(request, now + timedelta(seconds=1))
        assert replay == submitted
        with pytest.raises(CodingJobConflictError):
            await repository.submit(
                request.model_copy(update={"objective": "Different objective"}), now
            )

        claims = await asyncio.gather(
            repository.claim("worker-a", now, now + timedelta(seconds=10)),
            repository.claim("worker-b", now, now + timedelta(seconds=10)),
        )
        won = [claim for claim in claims if claim is not None]
        assert len(won) == 1
        claim = won[0]
        renewed = await repository.renew(
            claim, now + timedelta(seconds=5), now + timedelta(seconds=20)
        )
        assert renewed.attempt == claim.attempt == 1
        await first.close()

        restarted: AsyncMongoClient[dict[str, Any]] = AsyncMongoClient(
            uri, server_api=ServerApi("1"), tz_aware=True
        )
        restarted_database = restarted[database_name]
        restarted_repository = MongoCodingJobRepository(restarted_database)
        result = CodingJobResult(
            job_id=request.job_id,
            outcome=CodingJobOutcome.FIXED,
            summary="Worker repaired",
            changed_paths=["src/worker.py"],
            checks=["pytest tests/test_worker.py"],
        )
        completed = await restarted_repository.complete(renewed, result, now + timedelta(seconds=6))
        duplicate = await restarted_repository.complete(renewed, result, now + timedelta(seconds=7))
        assert duplicate == completed
        assert duplicate.job.status == CodingJobStatus.COMPLETED
        assert duplicate.job.outcome == CodingJobOutcome.FIXED
        assert duplicate.run.status.value == "waiting_for_jobs"
        consumer = await restarted_repository.claim_terminal_run(
            now + timedelta(seconds=8), now + timedelta(seconds=38)
        )
        assert consumer is not None and consumer.job.job_id == request.job_id
        fingerprint = coding_job_completion_fingerprint(consumer.job)
        consumed = await restarted_repository.finish_consumption(
            consumer,
            status=RunStatus.COMPLETED,
            resume_turn_id=f"{identity}-resume",
            completion_fingerprint=fingerprint,
            publication_outcome=MockPublicationOutcome(
                status="draft_created",
                reference=f"mock://draft-pr/{request.job_id}",
                repository_revision=request.repository_revision,
            ),
            memory_id=None,
            now=now + timedelta(seconds=9),
        )
        assert consumed.run.schema_version == "2"
        assert consumed.job.consumed_by_turn_id == f"{identity}-resume"

        expired_request = _request(f"{identity}-expired", deadline_at=now + timedelta(minutes=5))
        await restarted_repository.submit(expired_request, now)
        expired_first = await restarted_repository.claim(
            "worker-a", now, now + timedelta(seconds=10)
        )
        assert expired_first is not None
        assert (
            await restarted_repository.claim(
                "worker-b", now + timedelta(seconds=9), now + timedelta(seconds=19)
            )
            is None
        )
        expired_second = await restarted_repository.claim(
            "worker-b", now + timedelta(seconds=11), now + timedelta(seconds=21)
        )
        assert expired_second is not None and expired_second.attempt == 2
        with pytest.raises(StaleCodingJobClaimError):
            await restarted_repository.complete(
                expired_first,
                CodingJobResult(
                    job_id=expired_request.job_id,
                    outcome=CodingJobOutcome.NO_FIX_FOUND,
                    summary="Stale result",
                ),
                now + timedelta(seconds=12),
            )

        cancelled_request = _request(
            f"{identity}-cancelled", deadline_at=now + timedelta(minutes=5)
        )
        await restarted_repository.submit(cancelled_request, now)
        cancelled = await restarted_repository.cancel(
            cancelled_request.job_id, now + timedelta(seconds=1)
        )
        assert cancelled.job.status == CodingJobStatus.CANCELLED

        timeout_request = _request(f"{identity}-timeout", deadline_at=now + timedelta(seconds=1))
        await restarted_repository.submit(timeout_request, now)
        await restarted_repository.reconcile(now + timedelta(seconds=2))
        timed_out = await restarted_repository.read(timeout_request.job_id)
        assert timed_out is not None and timed_out.job.status == CodingJobStatus.TIMED_OUT

        invalid_request = _request(f"{identity}-invalid", deadline_at=now + timedelta(minutes=5))
        await restarted_repository.submit(invalid_request, now)
        with pytest.raises(WriteError):
            await restarted_database["coding_jobs"].update_one(
                {"_id": invalid_request.job_id},
                {
                    "$set": {
                        "status": "completed",
                        "completed_at": now + timedelta(seconds=1),
                    }
                },
            )

        await restarted_database["coding_jobs"].delete_many(
            {
                "_id": {
                    "$in": [
                        request.job_id,
                        expired_request.job_id,
                        cancelled_request.job_id,
                        timeout_request.job_id,
                        invalid_request.job_id,
                    ]
                }
            }
        )
        await restarted_database["runs"].delete_many({"_id": {"$regex": f"^{identity}"}})
        await restarted.close()

    asyncio.run(exercise())

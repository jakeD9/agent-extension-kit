import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from pydantic import ValidationError
from team_agent_contracts import Citation, SkillFileManifest, SkillLock, SkillLockPackage
from team_agent_runtime import (
    CodingJobConflictError,
    CodingJobFailure,
    CodingJobInputRequest,
    CodingJobOutcome,
    CodingJobRequest,
    CodingJobResult,
    CodingJobStatus,
    InMemoryCodingJobRepository,
    MockCodingExecutor,
    MockCodingHarness,
    StaleCodingJobClaimError,
)


def _lock(project: str = "platform", revision: str = "content-abc") -> SkillLock:
    citation = Citation(
        repository="company/agent-extension",
        path="extension/skills/diagnose-and-fix/SKILL.md",
        revision=revision,
    )
    return SkillLock(
        project=project,
        target="generic",
        catalog_revision=revision,
        packages=[
            SkillLockPackage(
                package_id=f"sha256:{'a' * 64}",
                name="diagnose-and-fix",
                description="Diagnose and fix a defect.",
                version="1.0.0",
                source_revision=revision,
                files=[SkillFileManifest(path="SKILL.md", sha256="b" * 64, size=10)],
                citation=citation,
            )
        ],
    )


def _request(
    job_id: str = "job-1",
    *,
    run_id: str = "run-1",
    key: str = "submit-1",
    objective: str = "Repair the worker",
    deadline: datetime | None = None,
) -> CodingJobRequest:
    return CodingJobRequest(
        job_id=job_id,
        run_id=run_id,
        conversation_id="conversation-1",
        source_turn_id="turn-1",
        submission_idempotency_key=key,
        project="platform",
        repository="company/service",
        repository_revision="commit-123",
        objective=objective,
        content_revision="content-abc",
        selected_skill_lock=_lock(),
        deadline_at=deadline or datetime(2026, 1, 1, 1, tzinfo=UTC),
    )


def _result(job_id: str = "job-1", summary: str = "Worker repaired") -> CodingJobResult:
    return CodingJobResult(
        job_id=job_id,
        outcome=CodingJobOutcome.FIXED,
        summary=summary,
        changed_paths=["src/worker.py"],
        checks=["pytest tests/test_worker.py"],
    )


def test_contracts_are_strict_snake_case_and_pin_project_revisions() -> None:
    request = _request()

    assert request.model_dump(mode="json")["submission_idempotency_key"] == "submit-1"
    assert request.model_dump(mode="json")["repository_revision"] == "commit-123"
    with pytest.raises(ValidationError):
        CodingJobRequest.model_validate(
            {**request.model_dump(mode="json"), "submissionIdempotencyKey": "wrong"}
        )
    with pytest.raises(ValidationError, match="content_revision"):
        CodingJobRequest.model_validate(
            {**request.model_dump(mode="json"), "content_revision": "other"}
        )
    with pytest.raises(ValidationError, match="project"):
        CodingJobRequest.model_validate(
            {
                **request.model_dump(mode="json"),
                "project": "other",
            }
        )
    for unsafe_path in (
        "../secret",
        "/etc/passwd",
        "src\\worker.py",
        "src//worker.py",
        "C:/Windows/system.ini",
        "src/worker.py\0hidden",
    ):
        with pytest.raises(ValidationError, match="changed_paths"):
            CodingJobResult.model_validate(
                {**_result().model_dump(mode="json"), "changed_paths": [unsafe_path]}
            )


def test_submission_is_idempotent_by_project_key_and_allows_many_jobs_per_run() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        first = await repository.submit(_request(), now)
        replay = await repository.submit(_request(), now + timedelta(seconds=1))
        second = await repository.submit(_request("job-2", run_id="run-1", key="submit-2"), now)

        assert replay == first
        assert second.run.run_id == first.run.run_id
        assert second.job.job_id == "job-2"
        with pytest.raises(CodingJobConflictError):
            await repository.submit(_request(objective="Different request"), now)

    asyncio.run(check())


def test_concurrent_claim_has_one_winner_and_renewal_keeps_attempt() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await repository.submit(_request(), now)
        claims = await asyncio.gather(
            repository.claim("worker-a", now, now + timedelta(seconds=10)),
            repository.claim("worker-b", now, now + timedelta(seconds=10)),
        )
        claimed = [claim for claim in claims if claim is not None]
        assert len(claimed) == 1
        assert claimed[0].attempt == 1

        renewed = await repository.renew(
            claimed[0], now + timedelta(seconds=5), now + timedelta(seconds=15)
        )
        assert renewed.attempt == 1
        assert renewed.lease_expires_at == now + timedelta(seconds=15)

    asyncio.run(check())


def test_expired_lease_is_reclaimed_and_fences_old_attempt() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await repository.submit(_request(), now)
        first = await repository.claim("worker-a", now, now + timedelta(seconds=10))
        assert first is not None
        assert (
            await repository.claim(
                "worker-b", now + timedelta(seconds=9), now + timedelta(seconds=19)
            )
            is None
        )
        second = await repository.claim(
            "worker-b", now + timedelta(seconds=11), now + timedelta(seconds=21)
        )
        assert second is not None and second.attempt == 2
        with pytest.raises(StaleCodingJobClaimError):
            await repository.complete(first, _result(), now + timedelta(seconds=12))
        completed = await repository.complete(second, _result(), now + timedelta(seconds=12))
        assert completed.job.status == CodingJobStatus.COMPLETED
        with pytest.raises(StaleCodingJobClaimError):
            await repository.complete(first, _result(), now + timedelta(seconds=13))

    asyncio.run(check())


def test_running_job_cannot_renew_or_complete_past_deadline() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await repository.submit(
            _request(deadline=now + timedelta(seconds=5)),
            now,
        )
        claim = await repository.claim("worker", now, now + timedelta(seconds=20))
        assert claim is not None
        assert claim.lease_expires_at == now + timedelta(seconds=5)

        with pytest.raises(StaleCodingJobClaimError):
            await repository.renew(claim, now + timedelta(seconds=6), now + timedelta(seconds=10))
        await repository.reconcile(now + timedelta(seconds=6))
        with pytest.raises(StaleCodingJobClaimError):
            await repository.complete(claim, _result(), now + timedelta(seconds=6))
        stored = await repository.read("job-1")
        assert stored is not None and stored.job.status == CodingJobStatus.TIMED_OUT

    asyncio.run(check())


def test_completion_is_idempotent_only_for_the_same_attempt_and_result() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await repository.submit(_request(), now)
        claim = await repository.claim("worker", now, now + timedelta(seconds=10))
        assert claim is not None
        first = await repository.complete(claim, _result(), now + timedelta(seconds=1))
        replay = await repository.complete(claim, _result(), now + timedelta(seconds=2))

        assert replay == first
        assert replay.job.outcome == CodingJobOutcome.FIXED
        with pytest.raises(CodingJobConflictError):
            await repository.complete(
                claim, _result(summary="A contradictory result"), now + timedelta(seconds=2)
            )

    asyncio.run(check())


def test_failure_needs_input_cancellation_and_deadline_are_separate_terminal_states() -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request("failed", key="failed"), now)
        failed_claim = await repository.claim("worker", now, now + timedelta(seconds=10))
        assert failed_claim is not None
        failed = await repository.fail(
            failed_claim,
            CodingJobFailure(code="runner_failed", message="Runner failed."),
            now + timedelta(seconds=1),
        )
        assert failed.job.status == CodingJobStatus.FAILED
        assert failed.job.outcome is None

        await repository.submit(_request("input", key="input"), now)
        input_claim = await repository.claim("worker", now, now + timedelta(seconds=10))
        assert input_claim is not None
        needs_input = await repository.request_input(
            input_claim,
            CodingJobInputRequest(question="Which service owns this configuration?"),
            now + timedelta(seconds=1),
        )
        assert needs_input.job.status == CodingJobStatus.NEEDS_INPUT

        await repository.submit(_request("cancelled", key="cancelled"), now)
        cancelled = await repository.cancel("cancelled", now + timedelta(seconds=1))
        assert cancelled.job.status == CodingJobStatus.CANCELLED

        await repository.submit(
            _request(
                "timeout",
                key="timeout",
                deadline=now + timedelta(seconds=2),
            ),
            now,
        )
        assert await repository.reconcile(now + timedelta(seconds=3)) == 1
        timed_out = await repository.read("timeout")
        assert timed_out is not None
        assert timed_out.job.status == CodingJobStatus.TIMED_OUT

    asyncio.run(check())


def test_cancellation_wins_race_and_late_completion_is_rejected() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await repository.submit(_request(), now)
        claim = await repository.claim("worker", now, now + timedelta(seconds=10))
        assert claim is not None
        await repository.cancel("job-1", now + timedelta(seconds=1))
        with pytest.raises(StaleCodingJobClaimError):
            await repository.complete(claim, _result(), now + timedelta(seconds=2))

    asyncio.run(check())


def test_mock_executor_validates_result_and_persists_bounded_failure() -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        good_repository = InMemoryCodingJobRepository()
        await good_repository.submit(_request(), now)
        good = await MockCodingExecutor(
            good_repository,
            MockCodingHarness(_result()),
            worker_id="mock-worker",
            clock=lambda: now + timedelta(seconds=1),
        ).execute_one()
        assert good is not None and good.job.status == CodingJobStatus.COMPLETED

        bad_repository = InMemoryCodingJobRepository()
        await bad_repository.submit(_request("bad", key="bad"), now)
        bad = await MockCodingExecutor(
            bad_repository,
            MockCodingHarness({"job_id": "bad", "outcome": "invented"}),
            worker_id="mock-worker",
            clock=lambda: now + timedelta(seconds=1),
        ).execute_one()
        assert bad is not None and bad.job.status == CodingJobStatus.FAILED
        assert bad.job.failure is not None
        assert bad.job.failure.code == "invalid_harness_result"

        wrong_repository = InMemoryCodingJobRepository()
        await wrong_repository.submit(_request("expected", key="wrong-id"), now)
        wrong = await MockCodingExecutor(
            wrong_repository,
            MockCodingHarness(_result("other")),
            worker_id="mock-worker",
            clock=lambda: now + timedelta(seconds=1),
        ).execute_one()
        assert wrong is not None and wrong.job.status == CodingJobStatus.FAILED
        assert wrong.job.failure is not None
        assert wrong.job.failure.code == "invalid_harness_result"

    asyncio.run(check())


def test_executor_shutdown_leaves_running_job_for_lease_recovery() -> None:
    class _BlockedHarness:
        def __init__(self) -> None:
            self.started = asyncio.Event()

        async def execute(self, request: CodingJobRequest) -> object:
            del request
            self.started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(), now)
        harness = _BlockedHarness()
        executor = MockCodingExecutor(
            repository,
            harness,
            worker_id="mock-worker",
            clock=lambda: now + timedelta(seconds=1),
        )
        task = asyncio.create_task(executor.execute_one())
        await harness.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

        stored = await repository.read("job-1")
        assert stored is not None
        assert stored.job.status == CodingJobStatus.RUNNING
        assert stored.job.failure is None

    asyncio.run(check())


def test_mock_executor_renews_lease_while_harness_is_running() -> None:
    class _SlowHarness:
        async def execute(self, request: CodingJobRequest) -> object:
            await asyncio.sleep(0.04)
            return _result(request.job_id)

    class _Clock:
        def __init__(self, current: datetime) -> None:
            self.current = current

        def __call__(self) -> datetime:
            self.current += timedelta(milliseconds=5)
            return self.current

    async def check() -> None:
        start = datetime(2026, 1, 1, tzinfo=UTC)
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(), start)
        completed = await MockCodingExecutor(
            repository,
            _SlowHarness(),
            worker_id="mock-worker",
            lease_ttl=timedelta(milliseconds=20),
            clock=_Clock(start),
        ).execute_one()

        assert completed is not None
        assert completed.job.status == CodingJobStatus.COMPLETED
        assert completed.job.attempt == 1

    asyncio.run(check())


class _MigrationCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], str, bool]] = []
        self.upserts: list[tuple[dict[str, object], dict[str, object]]] = []

    async def create_index(
        self, keys: list[tuple[str, int]], *, name: str, unique: bool = False
    ) -> str:
        self.indexes.append((keys, name, unique))
        return name

    async def update_one(
        self, query: dict[str, object], update: dict[str, object], *, upsert: bool = False
    ) -> object:
        assert upsert
        self.upserts.append((query, update))
        return object()


class _MigrationDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _MigrationCollection] = {}
        self.validators: dict[str, dict[str, Any]] = {}

    async def list_collection_names(self) -> list[str]:
        return list(self.collections)

    async def create_collection(
        self,
        name: str,
        *,
        validator: dict[str, Any],
        validationLevel: str,
        validationAction: str,
    ) -> None:
        assert validationLevel == "strict"
        assert validationAction == "error"
        self.collections[name] = _MigrationCollection()
        self.validators[name] = validator

    async def command(self, command: dict[str, Any]) -> dict[str, int]:
        self.validators[command["collMod"]] = command["validator"]
        return {"ok": 1}

    def __getitem__(self, name: str) -> _MigrationCollection:
        return self.collections[name]


def test_mongo_migration_has_strict_snake_case_state_and_nonunique_run_lookup() -> None:
    from team_agent_runtime.mongo_jobs import MongoCodingJobRepository

    async def check() -> None:
        database = _MigrationDatabase()
        repository = MongoCodingJobRepository(database)  # type: ignore[arg-type]
        await repository.migrate(datetime(2026, 1, 1, tzinfo=UTC))

        assert set(database.validators) == {"runs", "coding_jobs", "schema_migrations"}
        schema = database.validators["coding_jobs"]["$jsonSchema"]
        properties = schema["properties"]
        assert "submission_idempotency_key" in properties
        assert "lease_expires_at" in properties
        assert "outcome" in properties
        assert "oneOf" in schema
        assert not any(any(character.isupper() for character in key) for key in properties)
        indexes = database["coding_jobs"].indexes
        assert (
            [("project", 1), ("submission_idempotency_key", 1)],
            "project_submission_unique",
            True,
        ) in indexes
        assert ([("run_id", 1), ("created_at", 1)], "run_created", False) in indexes
        assert database["schema_migrations"].upserts[0][0] == {"_id": "runtime-jobs-schema-v1"}

    asyncio.run(check())

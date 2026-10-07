import asyncio
import json
from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from hashlib import sha256
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
    MockPublicationOutcome,
    RunRecord,
    RunStatus,
    StaleCodingJobClaimError,
    coding_job_completion_fingerprint,
    coding_job_request_fingerprint,
    coding_job_result_fingerprint,
)
from team_agent_runtime.jobs import mock_publication_outcome


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


@pytest.mark.parametrize(
    "partial_identity", [{"completion_job_id": "job-1"}, {"completion_attempt": 1}]
)
def test_waiting_run_rejects_partial_completion_identity(
    partial_identity: dict[str, object],
) -> None:
    with pytest.raises(ValidationError, match="waiting runs"):
        RunRecord(
            run_id="run-1",
            project="platform",
            conversation_id="conversation-1",
            source_turn_id="turn-1",
            status=RunStatus.WAITING_FOR_JOBS,
            claim_generation=0,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
            updated_at=datetime(2026, 1, 1, tzinfo=UTC),
            **partial_identity,
        )


def test_empty_planning_citations_preserve_the_s11_request_fingerprint() -> None:
    request = _request()
    legacy_payload = request.model_dump(mode="json")
    legacy_payload["schema_version"] = "1"
    del legacy_payload["execution_policy"]
    del legacy_payload["planning_citations"]
    legacy = (
        "sha256:"
        + sha256(
            json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )
    assert coding_job_request_fingerprint(request) == legacy
    assert (
        coding_job_request_fingerprint(
            request.model_copy(
                update={
                    "planning_citations": [
                        Citation(
                            repository="team/knowledge",
                            path="adr/worker.md",
                            revision="content-abc",
                        )
                    ]
                }
            )
        )
        != legacy
    )


def test_empty_reusable_lesson_preserves_the_s11_result_fingerprint() -> None:
    result = _result()
    legacy_payload = result.model_dump(mode="json")
    legacy_payload["schema_version"] = "1"
    del legacy_payload["artifacts"]
    del legacy_payload["reusable_lesson"]
    legacy = (
        "sha256:"
        + sha256(
            json.dumps(legacy_payload, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
    )

    assert coding_job_result_fingerprint(result) == legacy


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


def test_same_stable_worker_recovers_live_attempt_without_incrementing_fence() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await repository.submit(_request(), now)
        claimed = await repository.claim("worker-a", now, now + timedelta(seconds=20))
        assert claimed is not None

        recovered = await repository.recover(
            "worker-a", now + timedelta(seconds=5), now + timedelta(seconds=30)
        )

        assert recovered is not None
        assert recovered.attempt == claimed.attempt == 1
        assert recovered.lease_expires_at == now + timedelta(seconds=30)
        assert (
            await repository.recover(
                "worker-b", now + timedelta(seconds=6), now + timedelta(seconds=30)
            )
            is None
        )

    asyncio.run(check())


def test_harness_scoped_workers_skip_each_others_mixed_queue_jobs() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        mock_request = _request()
        codex_request = _request("job-2", run_id="run-2", key="submit-2").model_copy(
            update={"harness": "codex"}
        )
        await repository.submit(mock_request, now)
        await repository.submit(codex_request, now + timedelta(seconds=1))

        codex_claim = await repository.claim(
            "codex-worker", now + timedelta(seconds=2), now + timedelta(seconds=20), harness="codex"
        )
        assert codex_claim is not None and codex_claim.job_id == "job-2"
        untouched_mock = await repository.read("job-1")
        assert untouched_mock is not None
        assert untouched_mock.job.status == CodingJobStatus.QUEUED
        assert untouched_mock.job.attempt == 0

        mock_claim = await repository.claim(
            "mock-worker", now + timedelta(seconds=2), now + timedelta(seconds=20), harness="mock"
        )
        assert mock_claim is not None and mock_claim.job_id == "job-1"
        assert mock_claim.attempt == codex_claim.attempt == 1

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


def test_consumption_rejects_state_fingerprint_and_publication_not_derived_from_job() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await repository.submit(_request(), now)
        job_claim = await repository.claim("worker", now, now + timedelta(seconds=10))
        assert job_claim is not None
        await repository.complete(job_claim, _result(), now + timedelta(seconds=1))
        claim = await repository.claim_terminal_run(
            now + timedelta(seconds=2), now + timedelta(seconds=30)
        )
        assert claim is not None
        fingerprint = coding_job_completion_fingerprint(claim.job)
        publication = mock_publication_outcome(claim.job)

        with pytest.raises(CodingJobConflictError, match="status"):
            await repository.finish_consumption(
                claim,
                status=RunStatus.NEEDS_INPUT,
                resume_turn_id="resume-1",
                completion_fingerprint=fingerprint,
                publication_outcome=publication,
                memory_id=None,
                now=now + timedelta(seconds=3),
            )
        with pytest.raises(CodingJobConflictError, match="fingerprint"):
            await repository.finish_consumption(
                claim,
                status=RunStatus.COMPLETED,
                resume_turn_id="resume-1",
                completion_fingerprint="sha256:bogus",
                publication_outcome=publication,
                memory_id=None,
                now=now + timedelta(seconds=3),
            )
        with pytest.raises(CodingJobConflictError, match="publication"):
            await repository.finish_consumption(
                claim,
                status=RunStatus.COMPLETED,
                resume_turn_id="resume-1",
                completion_fingerprint=fingerprint,
                publication_outcome=MockPublicationOutcome(
                    status="not_created",
                    reason="wrong",
                    repository_revision="commit-123",
                ),
                memory_id=None,
                now=now + timedelta(seconds=3),
            )

    asyncio.run(check())


def test_s12_consumer_skips_multi_job_run_and_claims_later_single_job() -> None:
    async def check() -> None:
        repository = InMemoryCodingJobRepository()
        now = datetime(2026, 1, 1, tzinfo=UTC)
        await repository.submit(_request(), now)
        await repository.submit(_request("job-2", run_id="run-1", key="submit-2"), now)
        for job_id in ("job-1", "job-2"):
            claim = await repository.claim("worker", now, now + timedelta(seconds=10))
            assert claim is not None and claim.job_id == job_id
            await repository.complete(claim, _result(job_id), now + timedelta(seconds=1))

        valid_request = _request("job-valid", run_id="run-valid", key="submit-valid")
        await repository.submit(valid_request, now + timedelta(seconds=2))
        valid_claim = await repository.claim(
            "worker", now + timedelta(seconds=2), now + timedelta(seconds=10)
        )
        assert valid_claim is not None and valid_claim.job_id == "job-valid"
        await repository.complete(valid_claim, _result("job-valid"), now + timedelta(seconds=3))

        claimed = await repository.claim_terminal_run(
            now + timedelta(seconds=4), now + timedelta(seconds=30)
        )
        assert claimed is not None and claimed.job.job_id == "job-valid"

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
        self.dropped_indexes: list[str] = []
        self.upserts: list[tuple[dict[str, object], dict[str, object]]] = []
        self.updates: list[tuple[dict[str, object], dict[str, object]]] = []
        self.finds: list[dict[str, object]] = []

    async def create_index(
        self, keys: list[tuple[str, int]], *, name: str, unique: bool = False
    ) -> str:
        self.indexes.append((keys, name, unique))
        return name

    async def index_information(self) -> dict[str, dict[str, object]]:
        return {name: {"key": keys, "unique": unique} for keys, name, unique in self.indexes}

    async def drop_index(self, name: str) -> None:
        self.dropped_indexes.append(name)
        self.indexes = [index for index in self.indexes if index[1] != name]

    async def update_one(
        self, query: dict[str, object], update: dict[str, object], *, upsert: bool = False
    ) -> object:
        if upsert:
            self.upserts.append((query, update))
        else:
            self.updates.append((query, update))
        return object()

    async def update_many(self, query: dict[str, object], update: dict[str, object]) -> object:
        self.updates.append((query, update))
        return object()

    async def find(self, query: dict[str, object]) -> AsyncIterator[dict[str, object]]:
        self.finds.append(query)
        if False:
            yield {}


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


class _MalformedLegacyJobCollection(_MigrationCollection):
    async def find(self, query: dict[str, object]) -> AsyncIterator[dict[str, object]]:
        self.finds.append(query)
        yield {"_id": "malformed", "schema_version": "2"}


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
        assert "consumed_at" in properties
        assert "oneOf" in schema
        assert "claim_generation" in database.validators["runs"]["$jsonSchema"]["properties"]
        assert not any(any(character.isupper() for character in key) for key in properties)
        indexes = database["coding_jobs"].indexes
        assert (
            [("project", 1), ("submission_idempotency_key", 1)],
            "project_submission_unique",
            True,
        ) in indexes
        assert ([("run_id", 1), ("created_at", 1)], "run_created", False) in indexes
        assert database["schema_migrations"].upserts[0][0] == {"_id": "runtime-jobs-schema-v1"}
        assert database["schema_migrations"].upserts[1][0] == {"_id": "runtime-jobs-schema-v2"}
        assert database["schema_migrations"].upserts[2][0] == {"_id": "runtime-jobs-schema-v3"}

    asyncio.run(check())


def test_mongo_v3_migration_relaxes_backfills_then_reinstalls_strict_validators() -> None:
    from team_agent_runtime.mongo_jobs import MongoCodingJobRepository

    async def check() -> None:
        database = _MigrationDatabase()
        database.collections = {
            "runs": _MigrationCollection(),
            "coding_jobs": _MigrationCollection(),
            "schema_migrations": _MigrationCollection(),
        }
        repository = MongoCodingJobRepository(database)  # type: ignore[arg-type]
        await repository.migrate(datetime(2026, 1, 1, tzinfo=UTC))

        run_update = database["runs"].updates[0]
        assert run_update[0] == {"schema_version": "1"}
        assert run_update[1]["$set"]["schema_version"] == "2"  # type: ignore[index]
        assert database["coding_jobs"].finds == [{"schema_version": {"$in": ["1", "2"]}}]
        assert database.validators["runs"]["$jsonSchema"]["properties"]["schema_version"] == {
            "enum": ["2"]
        }
        assert database.validators["coding_jobs"]["$jsonSchema"]["properties"][
            "schema_version"
        ] == {"enum": ["3"]}

    asyncio.run(check())


def test_mongo_v3_migration_replaces_the_legacy_claim_index() -> None:
    from team_agent_runtime.mongo_jobs import MongoCodingJobRepository

    async def check() -> None:
        database = _MigrationDatabase()
        database.collections = {
            "runs": _MigrationCollection(),
            "coding_jobs": _MigrationCollection(),
            "schema_migrations": _MigrationCollection(),
        }
        database["coding_jobs"].indexes.append(
            (
                [("status", 1), ("deadline_at", 1), ("created_at", 1)],
                "claimable_deadline_created",
                False,
            )
        )

        await MongoCodingJobRepository(database).migrate(  # type: ignore[arg-type]
            datetime(2026, 1, 1, tzinfo=UTC)
        )

        assert database["coding_jobs"].dropped_indexes == ["claimable_deadline_created"]
        assert (
            [
                ("status", 1),
                ("request.harness", 1),
                ("deadline_at", 1),
                ("created_at", 1),
            ],
            "claimable_deadline_created",
            False,
        ) in database["coding_jobs"].indexes

    asyncio.run(check())


def test_mongo_v3_migration_restores_strict_validator_after_malformed_legacy_job() -> None:
    from team_agent_runtime.mongo_jobs import MongoCodingJobRepository

    async def check() -> None:
        database = _MigrationDatabase()
        database.collections = {
            "runs": _MigrationCollection(),
            "coding_jobs": _MalformedLegacyJobCollection(),
            "schema_migrations": _MigrationCollection(),
        }

        with pytest.raises(KeyError, match="request"):
            await MongoCodingJobRepository(database).migrate(  # type: ignore[arg-type]
                datetime(2026, 1, 1, tzinfo=UTC)
            )

        schema = database.validators["coding_jobs"]["$jsonSchema"]
        assert schema["properties"]["schema_version"] == {"enum": ["3"]}

    asyncio.run(check())


def test_mongo_v3_job_upgrade_is_atomic_and_preserves_legacy_fingerprints() -> None:
    from team_agent_runtime.mongo_jobs import _job_v3_updates

    request = _request()
    result = _result()
    legacy_request = request.model_dump(mode="json")
    legacy_request["schema_version"] = "1"
    del legacy_request["execution_policy"]
    legacy_result = result.model_dump(mode="json")
    legacy_result["schema_version"] = "1"
    del legacy_result["artifacts"]
    document: dict[str, Any] = {
        "schema_version": "2",
        "request": legacy_request,
        "request_fingerprint": coding_job_request_fingerprint(request),
        "result": legacy_result,
        "result_fingerprint": coding_job_result_fingerprint(result),
        "consumed_at": None,
        "consumed_by_turn_id": None,
    }

    updates = _job_v3_updates(document)

    assert updates["schema_version"] == "3"
    assert updates["request_fingerprint"] == document["request_fingerprint"]
    assert updates["result_fingerprint"] == document["result_fingerprint"]
    assert updates["request"]["schema_version"] == "2"  # type: ignore[index]
    assert updates["result"]["schema_version"] == "2"  # type: ignore[index]

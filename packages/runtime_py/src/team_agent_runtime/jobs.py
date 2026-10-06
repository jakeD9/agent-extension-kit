"""Provider-neutral durable coding-job contracts and reference implementation."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from hashlib import sha256
from pathlib import PurePosixPath
from typing import Any, Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from team_agent_contracts import Citation, SkillLock

Document = dict[str, Any]
DEFAULT_JOB_LEASE_TTL = timedelta(seconds=60)
MAX_JOB_DOCUMENT_BYTES = 8 * 1024 * 1024
TERMINAL_JOB_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled", "needs_input"})


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RunStatus(StrEnum):
    WAITING_FOR_JOBS = "waiting_for_jobs"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    NEEDS_INPUT = "needs_input"


class CodingJobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    NEEDS_INPUT = "needs_input"


class CodingJobOutcome(StrEnum):
    FIXED = "fixed"
    NO_FIX_FOUND = "no_fix_found"
    UNSAFE_TO_PROCEED = "unsafe_to_proceed"


class CodingJobRequest(_Contract):
    """Immutable input pinned before a disposable coding environment starts."""

    schema_version: Literal["1"] = "1"
    job_id: str = Field(min_length=1, max_length=160)
    run_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    source_turn_id: str = Field(min_length=1, max_length=128)
    submission_idempotency_key: str = Field(min_length=1, max_length=256)
    project: str = Field(min_length=1, max_length=200)
    repository: str = Field(min_length=1, max_length=500)
    repository_revision: str = Field(min_length=1, max_length=256)
    objective: str = Field(min_length=1, max_length=20_000)
    mode: Literal["fix"] = "fix"
    harness: Literal["mock"] = "mock"
    content_revision: str = Field(min_length=1, max_length=256)
    selected_skill_lock: SkillLock
    planning_citations: list[Citation] = Field(default_factory=list, max_length=20)
    deadline_at: AwareDatetime

    @model_validator(mode="after")
    def revisions_and_project_match(self) -> CodingJobRequest:
        if self.selected_skill_lock.catalog_revision != self.content_revision:
            raise ValueError("selected_skill_lock must match content_revision")
        if self.selected_skill_lock.project != self.project:
            raise ValueError("selected_skill_lock must match project")
        if self.selected_skill_lock.target != "generic":
            raise ValueError("coding jobs require the provider-neutral generic skill target")
        return self


class ReusableLesson(_Contract):
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=20_000)


class CodingJobResult(_Contract):
    schema_version: Literal["1"] = "1"
    job_id: str = Field(min_length=1, max_length=160)
    outcome: CodingJobOutcome
    summary: str = Field(min_length=1, max_length=20_000)
    changed_paths: list[str] = Field(default_factory=list, max_length=2_000)
    checks: list[str] = Field(default_factory=list, max_length=2_000)
    reusable_lesson: ReusableLesson | None = None

    @model_validator(mode="after")
    def unique_bounded_paths_and_checks(self) -> CodingJobResult:
        if len(set(self.changed_paths)) != len(self.changed_paths):
            raise ValueError("changed_paths must be unique")
        if len(set(self.checks)) != len(self.checks):
            raise ValueError("checks must be unique")
        if any(not _is_safe_relative_path(path) for path in self.changed_paths):
            raise ValueError("changed_paths must be bounded repository-relative paths")
        if any(not check or len(check) > 2_000 for check in self.checks):
            raise ValueError("checks must be non-empty and bounded")
        return self


class CodingJobFailure(_Contract):
    schema_version: Literal["1"] = "1"
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=500)
    retriable: bool = False


class CodingJobInputRequest(_Contract):
    schema_version: Literal["1"] = "1"
    question: str = Field(min_length=1, max_length=2_000)


class MockPublicationOutcome(_Contract):
    status: Literal["draft_created", "not_created"]
    reference: str | None = Field(default=None, max_length=500)
    reason: str | None = Field(default=None, max_length=500)
    repository_revision: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def reference_matches_status(self) -> MockPublicationOutcome:
        if (self.status == "draft_created") != (self.reference is not None):
            raise ValueError("only a created mock draft requires a reference")
        if (self.status == "not_created") != (self.reason is not None):
            raise ValueError("only a non-created mock draft requires a reason")
        return self


class RunRecord(_Contract):
    schema_version: Literal["2"] = "2"
    run_id: str
    project: str
    conversation_id: str
    source_turn_id: str
    status: RunStatus
    claim_generation: int = Field(ge=0)
    claim_expires_at: AwareDatetime | None = None
    completion_job_id: str | None = None
    completion_attempt: int | None = Field(default=None, ge=0)
    completion_fingerprint: str | None = None
    resume_turn_id: str | None = None
    memory_id: str | None = None
    publication_outcome: MockPublicationOutcome | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def state_is_consistent(self) -> RunRecord:
        if self.status == RunStatus.RUNNING:
            if self.claim_generation < 1 or self.claim_expires_at is None:
                raise ValueError("running runs require a live claim")
        elif self.claim_expires_at is not None:
            raise ValueError("only running runs may retain a claim")
        terminal = self.status not in {RunStatus.WAITING_FOR_JOBS, RunStatus.RUNNING}
        has_completion_identity = (
            self.completion_job_id is not None and self.completion_attempt is not None
        )
        if self.status == RunStatus.WAITING_FOR_JOBS and (
            self.completion_job_id is not None or self.completion_attempt is not None
        ):
            raise ValueError("waiting runs cannot retain a completion identity")
        if self.status != RunStatus.WAITING_FOR_JOBS and not has_completion_identity:
            raise ValueError("claimed and terminal runs require a completion identity")
        if terminal != (self.resume_turn_id is not None):
            raise ValueError("terminal runs require a resume turn")
        if terminal != (self.completion_fingerprint is not None):
            raise ValueError("terminal runs require a completion fingerprint")
        if terminal != (self.publication_outcome is not None):
            raise ValueError("terminal runs require a publication outcome")
        return self


class CodingJobRecord(_Contract):
    schema_version: Literal["2"] = "2"
    job_id: str
    run_id: str
    project: str
    submission_idempotency_key: str
    status: CodingJobStatus
    request_fingerprint: str
    request: CodingJobRequest
    deadline_at: AwareDatetime
    attempt: int = Field(ge=0)
    worker_id: str | None = None
    lease_expires_at: AwareDatetime | None = None
    cancel_requested_at: AwareDatetime | None = None
    result: CodingJobResult | None = None
    outcome: CodingJobOutcome | None = None
    result_fingerprint: str | None = None
    failure: CodingJobFailure | None = None
    input_request: CodingJobInputRequest | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    consumed_at: AwareDatetime | None = None
    consumed_by_turn_id: str | None = None

    @model_validator(mode="after")
    def state_is_consistent(self) -> CodingJobRecord:
        if self.status == CodingJobStatus.QUEUED:
            if self.worker_id is not None or self.lease_expires_at is not None:
                raise ValueError("queued jobs cannot retain a worker lease")
        elif self.status == CodingJobStatus.RUNNING:
            if self.attempt < 1 or self.worker_id is None or self.lease_expires_at is None:
                raise ValueError("running jobs require an attempted worker lease")
        elif self.worker_id is not None or self.lease_expires_at is not None:
            raise ValueError("terminal jobs cannot retain a worker lease")

        if self.status == CodingJobStatus.COMPLETED:
            if (
                self.result is None
                or self.result_fingerprint is None
                or self.outcome is None
                or self.result.outcome != self.outcome
            ):
                raise ValueError("completed jobs require a result")
        elif (
            self.result is not None
            or self.result_fingerprint is not None
            or self.outcome is not None
        ):
            raise ValueError("only completed jobs may have a result")
        if (self.status == CodingJobStatus.FAILED) != (self.failure is not None):
            raise ValueError("only failed jobs require a failure")
        if (self.status == CodingJobStatus.NEEDS_INPUT) != (self.input_request is not None):
            raise ValueError("only needs_input jobs require an input request")
        if self.status.value in TERMINAL_JOB_STATUSES and self.completed_at is None:
            raise ValueError("terminal jobs require completed_at")
        if self.status.value not in TERMINAL_JOB_STATUSES and self.completed_at is not None:
            raise ValueError("nonterminal jobs cannot have completed_at")
        if (self.consumed_at is None) != (self.consumed_by_turn_id is None):
            raise ValueError("consumption timestamp and turn must be stored together")
        if self.consumed_at is not None and self.status.value not in TERMINAL_JOB_STATUSES:
            raise ValueError("only terminal jobs may be consumed")
        return self


class CodingRunSnapshot(_Contract):
    run: RunRecord
    job: CodingJobRecord


@dataclass(frozen=True)
class CodingJobClaim:
    job_id: str
    run_id: str
    worker_id: str
    attempt: int
    request: CodingJobRequest
    lease_expires_at: datetime


@dataclass(frozen=True)
class RunConsumptionClaim:
    run_id: str
    job: CodingJobRecord
    generation: int
    expires_at: datetime


class CodingJobConflictError(RuntimeError):
    """An idempotency identity was reused with different content."""


class StaleCodingJobClaimError(RuntimeError):
    """A worker tried to mutate a job after losing its fenced attempt."""


class StaleRunConsumptionClaimError(RuntimeError):
    """A completion consumer tried to finalize a superseded run claim."""


class CodingJobNotFoundError(RuntimeError):
    """The requested run or coding job does not exist."""


class CodingJobRepository(Protocol):
    async def submit(self, request: CodingJobRequest, now: datetime) -> CodingRunSnapshot: ...

    async def claim(
        self, worker_id: str, now: datetime, expires_at: datetime
    ) -> CodingJobClaim | None: ...

    async def renew(
        self, claim: CodingJobClaim, now: datetime, expires_at: datetime
    ) -> CodingJobClaim: ...

    async def complete(
        self, claim: CodingJobClaim, result: CodingJobResult, now: datetime
    ) -> CodingRunSnapshot: ...

    async def fail(
        self, claim: CodingJobClaim, failure: CodingJobFailure, now: datetime
    ) -> CodingRunSnapshot: ...

    async def request_input(
        self, claim: CodingJobClaim, request: CodingJobInputRequest, now: datetime
    ) -> CodingRunSnapshot: ...

    async def cancel(self, job_id: str, now: datetime) -> CodingRunSnapshot: ...

    async def reconcile(self, now: datetime, *, limit: int = 100) -> int: ...

    async def read(self, job_id: str) -> CodingRunSnapshot | None: ...

    async def claim_terminal_run(
        self, now: datetime, expires_at: datetime, *, limit: int = 100
    ) -> RunConsumptionClaim | None: ...

    async def finish_consumption(
        self,
        claim: RunConsumptionClaim,
        *,
        status: RunStatus,
        resume_turn_id: str,
        completion_fingerprint: str,
        publication_outcome: MockPublicationOutcome,
        memory_id: str | None,
        now: datetime,
    ) -> CodingRunSnapshot: ...


class CodingHarness(Protocol):
    async def execute(self, request: CodingJobRequest) -> object: ...


def coding_job_request_fingerprint(request: CodingJobRequest) -> str:
    payload = request.model_dump(mode="json")
    # Preserve S11/v1 fingerprint identity for requests created before this optional field existed.
    if not payload["planning_citations"]:
        del payload["planning_citations"]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def coding_job_result_fingerprint(result: CodingJobResult) -> str:
    payload = result.model_dump(mode="json")
    # Preserve S11/v1 completion replay for results created before lessons existed.
    if payload["reusable_lesson"] is None:
        del payload["reusable_lesson"]
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def coding_job_completion_fingerprint(job: CodingJobRecord) -> str:
    if job.status.value not in TERMINAL_JOB_STATUSES:
        raise ValueError("only terminal jobs have completion identities")
    payload = {
        "job_id": job.job_id,
        "attempt": job.attempt,
        "status": job.status.value,
        "outcome": job.outcome.value if job.outcome is not None else None,
        "result": job.result.model_dump(mode="json") if job.result is not None else None,
        "failure": job.failure.model_dump(mode="json") if job.failure is not None else None,
        "input_request": (
            job.input_request.model_dump(mode="json") if job.input_request is not None else None
        ),
    }
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def mock_publication_outcome(job: CodingJobRecord) -> MockPublicationOutcome:
    if job.status == CodingJobStatus.COMPLETED and job.outcome == CodingJobOutcome.FIXED:
        return MockPublicationOutcome(
            status="draft_created",
            reference=f"mock://draft-pr/{job.job_id}",
            repository_revision=job.request.repository_revision,
        )
    reason = (
        job.outcome.value
        if job.status == CodingJobStatus.COMPLETED and job.outcome is not None
        else job.status.value
    )
    return MockPublicationOutcome(
        status="not_created",
        reason=reason,
        repository_revision=job.request.repository_revision,
    )


def completion_run_status(job: CodingJobRecord) -> RunStatus:
    return RunStatus(job.status.value)


def _validate_consumption_result(
    claim: RunConsumptionClaim,
    status: RunStatus,
    completion_fingerprint: str,
    publication_outcome: MockPublicationOutcome,
) -> None:
    if status != completion_run_status(claim.job):
        raise CodingJobConflictError("run status does not match the claimed job")
    if completion_fingerprint != coding_job_completion_fingerprint(claim.job):
        raise CodingJobConflictError("completion fingerprint does not match the claimed job")
    if publication_outcome != mock_publication_outcome(claim.job):
        raise CodingJobConflictError("publication outcome does not match the claimed job")


def _document_size(document: Mapping[str, object]) -> int:
    return len(json.dumps(document, default=str, separators=(",", ":")).encode())


def _assert_document_size(document: Mapping[str, object]) -> None:
    if _document_size(document) > MAX_JOB_DOCUMENT_BYTES:
        raise ValueError("coding job document exceeds the persistence limit")


def _run_document(request: CodingJobRequest, fingerprint: str, now: datetime) -> Document:
    del fingerprint
    return {
        "_id": request.run_id,
        "schema_version": "2",
        "project": request.project,
        "conversation_id": request.conversation_id,
        "source_turn_id": request.source_turn_id,
        "status": RunStatus.WAITING_FOR_JOBS.value,
        "claim_generation": 0,
        "claim_expires_at": None,
        "completion_job_id": None,
        "completion_attempt": None,
        "completion_fingerprint": None,
        "resume_turn_id": None,
        "memory_id": None,
        "publication_outcome": None,
        "created_at": now,
        "updated_at": now,
    }


def _job_document(request: CodingJobRequest, fingerprint: str, now: datetime) -> Document:
    return {
        "_id": request.job_id,
        "schema_version": "2",
        "run_id": request.run_id,
        "project": request.project,
        "submission_idempotency_key": request.submission_idempotency_key,
        "status": CodingJobStatus.QUEUED.value,
        "request_fingerprint": fingerprint,
        "request": request.model_dump(mode="json"),
        "deadline_at": request.deadline_at,
        "attempt": 0,
        "worker_id": None,
        "lease_expires_at": None,
        "cancel_requested_at": None,
        "result": None,
        "outcome": None,
        "result_fingerprint": None,
        "failure": None,
        "input_request": None,
        "created_at": now,
        "updated_at": now,
        "started_at": None,
        "completed_at": None,
        "consumed_at": None,
        "consumed_by_turn_id": None,
    }


def _run_model(document: Document) -> RunRecord:
    fields = {key: value for key, value in document.items() if key != "_id"}
    return RunRecord.model_validate({"run_id": document["_id"], **fields})


def _job_model(document: Document) -> CodingJobRecord:
    fields = {key: value for key, value in document.items() if key != "_id"}
    return CodingJobRecord.model_validate({"job_id": document["_id"], **fields})


def _snapshot(run: Document, job: Document) -> CodingRunSnapshot:
    return CodingRunSnapshot(run=_run_model(run), job=_job_model(job))


class InMemoryCodingJobRepository:
    """Reference semantics for deterministic tests and local mock execution."""

    def __init__(self) -> None:
        self._runs: dict[str, Document] = {}
        self._jobs: dict[str, Document] = {}
        self._submission_keys: dict[tuple[str, str], str] = {}
        self._mutex = asyncio.Lock()

    async def submit(self, request: CodingJobRequest, now: datetime) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        fingerprint = coding_job_request_fingerprint(request)
        run_document = _run_document(request, fingerprint, now)
        job_document = _job_document(request, fingerprint, now)
        _assert_document_size(run_document)
        _assert_document_size(job_document)
        async with self._mutex:
            submission_identity = (request.project, request.submission_idempotency_key)
            existing_job_id = self._submission_keys.get(submission_identity)
            if existing_job_id is not None:
                existing_job = self._jobs[existing_job_id]
                if existing_job["request_fingerprint"] != fingerprint:
                    raise CodingJobConflictError(
                        "submission_idempotency_key was already used for another request"
                    )
                return _snapshot(self._runs[str(existing_job["run_id"])], existing_job)
            if request.job_id in self._jobs:
                raise CodingJobConflictError("job_id was already used")
            existing_run = self._runs.get(request.run_id)
            if existing_run is not None and (
                existing_run["project"] != request.project
                or existing_run["conversation_id"] != request.conversation_id
                or existing_run["source_turn_id"] != request.source_turn_id
            ):
                raise CodingJobConflictError("run_id belongs to a different workflow source")
            if existing_run is None:
                self._runs[request.run_id] = run_document
            self._jobs[request.job_id] = job_document
            self._submission_keys[submission_identity] = request.job_id
            return _snapshot(self._runs[request.run_id], job_document)

    async def claim(
        self, worker_id: str, now: datetime, expires_at: datetime
    ) -> CodingJobClaim | None:
        _validate_lease_window(worker_id, now, expires_at)
        await self.reconcile(now)
        async with self._mutex:
            candidates = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job["status"] == CodingJobStatus.QUEUED.value
                    and job["cancel_requested_at"] is None
                    and job["deadline_at"] > now
                ),
                key=lambda job: (job["created_at"], job["_id"]),
            )
            if not candidates:
                return None
            job = candidates[0]
            request = CodingJobRequest.model_validate(job["request"])
            if expires_at > request.deadline_at:
                expires_at = request.deadline_at
            job.update(
                {
                    "status": CodingJobStatus.RUNNING.value,
                    "attempt": int(job["attempt"]) + 1,
                    "worker_id": worker_id,
                    "lease_expires_at": expires_at,
                    # This is the current fenced attempt's start, not first-ever start.
                    "started_at": now,
                    "updated_at": now,
                }
            )
            return _claim_from(job)

    async def renew(
        self, claim: CodingJobClaim, now: datetime, expires_at: datetime
    ) -> CodingJobClaim:
        _validate_lease_window(claim.worker_id, now, expires_at)
        await self.reconcile(now)
        async with self._mutex:
            job = self._require_active_claim(claim, now)
            deadline = CodingJobRequest.model_validate(job["request"]).deadline_at
            job["lease_expires_at"] = min(expires_at, deadline)
            job["updated_at"] = now
            return _claim_from(job)

    async def complete(
        self, claim: CodingJobClaim, result: CodingJobResult, now: datetime
    ) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        if result.job_id != claim.job_id:
            raise CodingJobConflictError("result belongs to a different job")
        fingerprint = coding_job_result_fingerprint(result)
        await self.reconcile(now)
        async with self._mutex:
            job = self._jobs.get(claim.job_id)
            if job is None:
                raise CodingJobNotFoundError(claim.job_id)
            if job["status"] == CodingJobStatus.COMPLETED.value:
                if job["attempt"] == claim.attempt and job["result_fingerprint"] == fingerprint:
                    return _snapshot(self._runs[claim.run_id], job)
                if job["attempt"] != claim.attempt:
                    raise StaleCodingJobClaimError("coding job claim has been superseded")
                raise CodingJobConflictError("completed job already has a different result")
            self._require_active_claim(claim, now)
            result_document = result.model_dump(mode="json")
            prospective = dict(job)
            prospective.update(
                {
                    "result": result_document,
                    "outcome": result.outcome.value,
                    "result_fingerprint": fingerprint,
                }
            )
            _assert_document_size(prospective)
            self._finish(job, CodingJobStatus.COMPLETED, now)
            job["result"] = result_document
            job["outcome"] = result.outcome.value
            job["result_fingerprint"] = fingerprint
            return _snapshot(self._runs[claim.run_id], job)

    async def fail(
        self, claim: CodingJobClaim, failure: CodingJobFailure, now: datetime
    ) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        await self.reconcile(now)
        async with self._mutex:
            job = self._require_active_claim(claim, now)
            failure_document = failure.model_dump(mode="json")
            prospective = dict(job)
            prospective["failure"] = failure_document
            _assert_document_size(prospective)
            self._finish(job, CodingJobStatus.FAILED, now)
            job["failure"] = failure_document
            return _snapshot(self._runs[claim.run_id], job)

    async def request_input(
        self, claim: CodingJobClaim, request: CodingJobInputRequest, now: datetime
    ) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        await self.reconcile(now)
        async with self._mutex:
            job = self._require_active_claim(claim, now)
            input_document = request.model_dump(mode="json")
            prospective = dict(job)
            prospective["input_request"] = input_document
            _assert_document_size(prospective)
            self._finish(job, CodingJobStatus.NEEDS_INPUT, now)
            job["input_request"] = input_document
            return _snapshot(self._runs[claim.run_id], job)

    async def cancel(self, job_id: str, now: datetime) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        async with self._mutex:
            job = self._jobs.get(job_id)
            if job is None:
                raise CodingJobNotFoundError(job_id)
            run = self._runs[str(job["run_id"])]
            if job["status"] not in TERMINAL_JOB_STATUSES:
                job["cancel_requested_at"] = now
                self._finish(job, CodingJobStatus.CANCELLED, now)
            return _snapshot(run, job)

    async def reconcile(self, now: datetime, *, limit: int = 100) -> int:
        _validate_timestamp("now", now)
        if limit < 1 or limit > 1_000:
            raise ValueError("reconcile limit must be between 1 and 1000")
        changed = 0
        async with self._mutex:
            candidates = sorted(
                self._jobs.values(), key=lambda job: (job["updated_at"], job["_id"])
            )
            for job in candidates:
                if changed >= limit or job["status"] in TERMINAL_JOB_STATUSES:
                    continue
                request = CodingJobRequest.model_validate(job["request"])
                if job["cancel_requested_at"] is not None:
                    self._finish(job, CodingJobStatus.CANCELLED, now)
                elif request.deadline_at <= now:
                    self._finish(job, CodingJobStatus.TIMED_OUT, now)
                elif (
                    job["status"] == CodingJobStatus.RUNNING.value
                    and job["lease_expires_at"] <= now
                ):
                    job.update(
                        {
                            "status": CodingJobStatus.QUEUED.value,
                            "worker_id": None,
                            "lease_expires_at": None,
                            "updated_at": now,
                        }
                    )
                else:
                    continue
                changed += 1
        return changed

    async def read(self, job_id: str) -> CodingRunSnapshot | None:
        async with self._mutex:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            return _snapshot(self._runs[str(job["run_id"])], job)

    async def claim_terminal_run(
        self, now: datetime, expires_at: datetime, *, limit: int = 100
    ) -> RunConsumptionClaim | None:
        _validate_consumption_window(now, expires_at, limit)
        async with self._mutex:
            candidates = sorted(
                (
                    job
                    for job in self._jobs.values()
                    if job["status"] in TERMINAL_JOB_STATUSES
                    and job["consumed_at"] is None
                    and (
                        self._runs[str(job["run_id"])]["status"] == RunStatus.WAITING_FOR_JOBS.value
                        or (
                            self._runs[str(job["run_id"])]["status"] == RunStatus.RUNNING.value
                            and self._runs[str(job["run_id"])]["claim_expires_at"] <= now
                        )
                    )
                ),
                key=lambda job: (job["completed_at"], job["_id"]),
            )[:limit]
            for job in candidates:
                run = self._runs[str(job["run_id"])]
                if sum(1 for item in self._jobs.values() if item["run_id"] == run["_id"]) != 1:
                    continue
                run.update(
                    {
                        "status": RunStatus.RUNNING.value,
                        "claim_generation": int(run["claim_generation"]) + 1,
                        "claim_expires_at": expires_at,
                        "completion_job_id": job["_id"],
                        "completion_attempt": job["attempt"],
                        "updated_at": now,
                    }
                )
                return RunConsumptionClaim(
                    run_id=str(run["_id"]),
                    job=_job_model(job),
                    generation=int(run["claim_generation"]),
                    expires_at=expires_at,
                )
            return None

    async def finish_consumption(
        self,
        claim: RunConsumptionClaim,
        *,
        status: RunStatus,
        resume_turn_id: str,
        completion_fingerprint: str,
        publication_outcome: MockPublicationOutcome,
        memory_id: str | None,
        now: datetime,
    ) -> CodingRunSnapshot:
        _validate_timestamp("now", now)
        _validate_consumption_result(claim, status, completion_fingerprint, publication_outcome)
        async with self._mutex:
            run = self._runs.get(claim.run_id)
            job = self._jobs.get(claim.job.job_id)
            if run is None or job is None:
                raise CodingJobNotFoundError(claim.job.job_id)
            if run["status"] not in {
                RunStatus.WAITING_FOR_JOBS.value,
                RunStatus.RUNNING.value,
            }:
                if (
                    run["status"] == status.value
                    and run["resume_turn_id"] == resume_turn_id
                    and run["completion_fingerprint"] == completion_fingerprint
                    and run["publication_outcome"] == publication_outcome.model_dump(mode="json")
                    and run["memory_id"] == memory_id
                    and job["consumed_by_turn_id"] == resume_turn_id
                ):
                    return _snapshot(run, job)
                raise StaleRunConsumptionClaimError("run completion was already consumed")
            if (
                run["status"] != RunStatus.RUNNING.value
                or run["claim_generation"] != claim.generation
                or run["claim_expires_at"] is None
                or run["claim_expires_at"] <= now
                or run["completion_job_id"] != job["_id"]
                or run["completion_attempt"] != job["attempt"]
                or job["consumed_at"] is not None
            ):
                raise StaleRunConsumptionClaimError("run consumption claim is stale or expired")
            job["consumed_at"] = now
            job["consumed_by_turn_id"] = resume_turn_id
            run.update(
                {
                    "status": status.value,
                    "claim_expires_at": None,
                    "completion_fingerprint": completion_fingerprint,
                    "resume_turn_id": resume_turn_id,
                    "memory_id": memory_id,
                    "publication_outcome": publication_outcome.model_dump(mode="json"),
                    "updated_at": now,
                }
            )
            return _snapshot(run, job)

    def _require_active_claim(self, claim: CodingJobClaim, now: datetime) -> Document:
        job = self._jobs.get(claim.job_id)
        if (
            job is None
            or job["run_id"] != claim.run_id
            or job["status"] != CodingJobStatus.RUNNING.value
            or job["worker_id"] != claim.worker_id
            or int(job["attempt"]) != claim.attempt
            or job["cancel_requested_at"] is not None
            or job["lease_expires_at"] <= now
            or CodingJobRequest.model_validate(job["request"]).deadline_at <= now
        ):
            raise StaleCodingJobClaimError("coding job claim is stale or expired")
        return job

    @staticmethod
    def _finish(job: Document, status: CodingJobStatus, now: datetime) -> None:
        job.update(
            {
                "status": status.value,
                "worker_id": None,
                "lease_expires_at": None,
                "updated_at": now,
                "completed_at": now,
            }
        )


class MockCodingHarness:
    """A deterministic disposable-runner stand-in with no repository access."""

    def __init__(self, result: object) -> None:
        self._result = result
        self.requests: list[CodingJobRequest] = []

    async def execute(self, request: CodingJobRequest) -> object:
        self.requests.append(request)
        return self._result


class MockCodingExecutor:
    """Supervisor boundary: claims, validates, and persists mock harness output."""

    def __init__(
        self,
        repository: CodingJobRepository,
        harness: CodingHarness,
        *,
        worker_id: str,
        lease_ttl: timedelta = DEFAULT_JOB_LEASE_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id is required")
        if lease_ttl <= timedelta(0):
            raise ValueError("lease_ttl must be positive")
        self._repository = repository
        self._harness = harness
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute_one(self) -> CodingRunSnapshot | None:
        now = self._clock()
        claim = await self._repository.claim(self._worker_id, now, now + self._lease_ttl)
        if claim is None:
            return None
        harness_task = asyncio.create_task(self._harness.execute(claim.request))
        heartbeat_seconds = max(self._lease_ttl.total_seconds() / 3, 0.01)
        try:
            while not harness_task.done():
                observed_at = self._clock()
                remaining_seconds = min(
                    (claim.lease_expires_at - observed_at).total_seconds(),
                    (claim.request.deadline_at - observed_at).total_seconds(),
                )
                wait_seconds = max(0.0, min(heartbeat_seconds, remaining_seconds))
                done, _ = await asyncio.wait({harness_task}, timeout=wait_seconds)
                if done:
                    break
                renewed_at = self._clock()
                try:
                    claim = await self._repository.renew(
                        claim, renewed_at, renewed_at + self._lease_ttl
                    )
                except StaleCodingJobClaimError:
                    harness_task.cancel()
                    with suppress(asyncio.CancelledError):
                        await harness_task
                    await self._repository.reconcile(renewed_at)
                    return await self._repository.read(claim.job_id)
        except asyncio.CancelledError:
            harness_task.cancel()
            with suppress(asyncio.CancelledError):
                await harness_task
            raise
        try:
            raw_result = await harness_task
            result = CodingJobResult.model_validate(raw_result)
            if result.job_id != claim.job_id:
                raise ValueError("mock harness result belongs to a different job")
        except Exception:
            failed_at = self._clock()
            try:
                return await self._repository.fail(
                    claim,
                    CodingJobFailure(
                        code="invalid_harness_result",
                        message="The mock harness did not return a valid structured result.",
                        retriable=False,
                    ),
                    failed_at,
                )
            except StaleCodingJobClaimError:
                await self._repository.reconcile(failed_at)
                return await self._repository.read(claim.job_id)
        completed_at = self._clock()
        try:
            return await self._repository.complete(claim, result, completed_at)
        except StaleCodingJobClaimError:
            await self._repository.reconcile(completed_at)
            return await self._repository.read(claim.job_id)


def _validate_lease_window(worker_id: str, now: datetime, expires_at: datetime) -> None:
    if not worker_id or len(worker_id) > 128:
        raise ValueError("worker_id must contain between 1 and 128 characters")
    _validate_timestamp("now", now)
    _validate_timestamp("lease expiry", expires_at)
    if expires_at <= now:
        raise ValueError("lease expiry must be after now")


def _validate_consumption_window(now: datetime, expires_at: datetime, limit: int) -> None:
    _validate_timestamp("now", now)
    _validate_timestamp("claim expiry", expires_at)
    if expires_at <= now:
        raise ValueError("claim expiry must be after now")
    if limit < 1 or limit > 1_000:
        raise ValueError("poll limit must be between 1 and 1000")


def _validate_timestamp(name: str, value: datetime) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")


def _is_safe_relative_path(value: str) -> bool:
    if not value or len(value) > 2_000 or "\\" in value or "\0" in value:
        return False
    path = PurePosixPath(value)
    has_drive_prefix = bool(path.parts and len(path.parts[0]) == 2 and path.parts[0][1] == ":")
    return (
        not path.is_absolute()
        and not has_drive_prefix
        and value == str(path)
        and value != "."
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _claim_from(job: Document) -> CodingJobClaim:
    return CodingJobClaim(
        job_id=str(job["_id"]),
        run_id=str(job["run_id"]),
        worker_id=str(job["worker_id"]),
        attempt=int(job["attempt"]),
        request=CodingJobRequest.model_validate(job["request"]),
        lease_expires_at=job["lease_expires_at"],
    )

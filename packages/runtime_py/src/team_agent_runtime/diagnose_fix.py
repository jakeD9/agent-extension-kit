"""Thin durable diagnose-and-fix workflow over conversations and coding jobs."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Literal, Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from team_agent_contracts import SharedMemoryCreateRequest

from team_agent_runtime import (
    AgentTurnRequest,
    AgentTurnStatus,
    JobCompletionContext,
)
from team_agent_runtime.conversations import DurableConversationRuntime
from team_agent_runtime.jobs import (
    CodingJobRecord,
    CodingJobRepository,
    CodingJobRequest,
    CodingJobStatus,
    CodingRunSnapshot,
    MockPublicationOutcome,
    RunConsumptionClaim,
    coding_job_completion_fingerprint,
    completion_run_status,
    mock_publication_outcome,
)

DEFAULT_RUN_CLAIM_TTL = timedelta(seconds=60)
DEFAULT_MEMORY_TTL = timedelta(days=90)


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DiagnoseAndFixRequest(_Contract):
    workflow_run_id: str = Field(min_length=1, max_length=128)
    planning_turn_id: str = Field(min_length=1, max_length=128)
    conversation_id: str = Field(min_length=1, max_length=128)
    project: str = Field(min_length=1, max_length=200)
    repository: str = Field(min_length=1, max_length=500)
    repository_revision: str = Field(min_length=1, max_length=256)
    objective: str = Field(min_length=1, max_length=20_000)
    deadline_at: AwareDatetime


class WorkflowPlanningError(RuntimeError):
    """The cited planning turn did not produce a job-safe plan."""


class SupplementalMemoryWriter(Protocol):
    async def create_memory(
        self, request: SharedMemoryCreateRequest, *, idempotency_key: str
    ) -> str: ...


def _stable_id(prefix: str, *parts: str) -> str:
    digest = sha256("\0".join(parts).encode()).hexdigest()
    return f"{prefix}-{digest[:40]}"


def _completion_context(
    job: CodingJobRecord, publication: MockPublicationOutcome
) -> JobCompletionContext:
    terminal_statuses: dict[
        CodingJobStatus,
        Literal["completed", "failed", "timed_out", "cancelled", "needs_input"],
    ] = {
        CodingJobStatus.COMPLETED: "completed",
        CodingJobStatus.FAILED: "failed",
        CodingJobStatus.TIMED_OUT: "timed_out",
        CodingJobStatus.CANCELLED: "cancelled",
        CodingJobStatus.NEEDS_INPUT: "needs_input",
    }
    status = terminal_statuses.get(job.status)
    if status is None:
        raise ValueError("only terminal jobs may resume a workflow")
    return JobCompletionContext(
        job_id=job.job_id,
        attempt=job.attempt,
        status=status,
        outcome=job.outcome.value if job.outcome is not None else None,
        summary=job.result.summary if job.result is not None else None,
        changed_paths=job.result.changed_paths if job.result is not None else [],
        checks=job.result.checks if job.result is not None else [],
        failure=job.failure.message if job.failure is not None else None,
        input_question=job.input_request.question if job.input_request is not None else None,
        mock_draft_pr_reference=publication.reference,
        repository=job.request.repository,
        repository_revision=job.request.repository_revision,
        pinned_content_revision=job.request.content_revision,
    )


class DiagnoseAndFixWorkflow:
    """Start one pinned mock job, then consume and report its result."""

    def __init__(
        self,
        conversations: DurableConversationRuntime,
        jobs: CodingJobRepository,
        *,
        memory_writer: SupplementalMemoryWriter | None = None,
        clock: Callable[[], datetime] | None = None,
        run_claim_ttl: timedelta = DEFAULT_RUN_CLAIM_TTL,
    ) -> None:
        if run_claim_ttl <= timedelta(0):
            raise ValueError("run_claim_ttl must be positive")
        self._conversations = conversations
        self._jobs = jobs
        self._memory_writer = memory_writer
        self._clock = clock or (lambda: datetime.now(UTC))
        self._run_claim_ttl = run_claim_ttl

    async def start(self, request: DiagnoseAndFixRequest) -> CodingRunSnapshot:
        planning = await self._conversations.execute(
            request.conversation_id,
            AgentTurnRequest(
                run_id=request.planning_turn_id,
                project=request.project,
                objective=request.objective,
                skill_names=["diagnose-and-fix"],
            ),
        )
        if (
            planning.status != AgentTurnStatus.COMPLETED
            or planning.selected_skill_lock is None
            or planning.content_revision is None
            or not planning.canonical_knowledge_citations
        ):
            raise WorkflowPlanningError("diagnose-and-fix planning did not produce cited evidence")
        job_id = _stable_id("job", request.project, request.workflow_run_id)
        submission_id = _stable_id("submit", request.project, request.workflow_run_id)
        return await self._jobs.submit(
            CodingJobRequest(
                job_id=job_id,
                run_id=request.workflow_run_id,
                conversation_id=request.conversation_id,
                source_turn_id=request.planning_turn_id,
                submission_idempotency_key=submission_id,
                project=request.project,
                repository=request.repository,
                repository_revision=request.repository_revision,
                objective=request.objective,
                content_revision=planning.content_revision,
                selected_skill_lock=planning.selected_skill_lock,
                planning_citations=planning.canonical_knowledge_citations,
                deadline_at=request.deadline_at,
            ),
            self._clock(),
        )

    async def consume_one(self, *, limit: int = 100) -> CodingRunSnapshot | None:
        now = self._clock()
        claim = await self._jobs.claim_terminal_run(now, now + self._run_claim_ttl, limit=limit)
        if claim is None:
            return None
        return await self._consume(claim)

    async def _consume(self, claim: RunConsumptionClaim) -> CodingRunSnapshot:
        job = claim.job
        fingerprint = coding_job_completion_fingerprint(job)
        resume_turn_id = _stable_id(
            "resume", claim.run_id, job.job_id, str(job.attempt), fingerprint
        )
        publication = mock_publication_outcome(job)
        memory_id = await self._create_memory(job, fingerprint)
        resume = await self._conversations.execute(
            job.request.conversation_id,
            AgentTurnRequest(
                run_id=resume_turn_id,
                project=job.project,
                objective="Report the coding-job result and the next actionable step.",
                skill_names=["diagnose-and-fix"],
                job_completion_context=_completion_context(job, publication),
            ),
            retry_retriable_failure=True,
        )
        if resume.status != AgentTurnStatus.COMPLETED:
            raise RuntimeError("the coordinator could not report the coding-job completion")
        return await self._jobs.finish_consumption(
            claim,
            status=completion_run_status(job),
            resume_turn_id=resume_turn_id,
            completion_fingerprint=fingerprint,
            publication_outcome=publication,
            memory_id=memory_id,
            now=self._clock(),
        )

    async def _create_memory(self, job: CodingJobRecord, fingerprint: str) -> str | None:
        if (
            job.status != CodingJobStatus.COMPLETED
            or job.result is None
            or job.result.reusable_lesson is None
        ):
            return None
        if self._memory_writer is None:
            raise RuntimeError("a reusable lesson requires a supplemental-memory writer")
        if not job.request.planning_citations:
            raise RuntimeError("a reusable lesson requires canonical planning evidence")
        if job.completed_at is None:
            raise RuntimeError("a reusable lesson requires the persisted job completion time")
        expires_at = job.completed_at + DEFAULT_MEMORY_TTL
        if expires_at <= self._clock():
            return None
        request = SharedMemoryCreateRequest(
            project=job.project,
            title=job.result.reusable_lesson.title,
            body=job.result.reusable_lesson.body,
            provenance=job.request.planning_citations[0],
            evidence=job.request.planning_citations,
            expires_at=expires_at,
        )
        return await self._memory_writer.create_memory(
            request,
            idempotency_key=_stable_id("lesson", job.run_id, fingerprint),
        )


__all__ = [
    "DEFAULT_MEMORY_TTL",
    "DEFAULT_RUN_CLAIM_TTL",
    "DiagnoseAndFixRequest",
    "DiagnoseAndFixWorkflow",
    "SupplementalMemoryWriter",
    "WorkflowPlanningError",
]

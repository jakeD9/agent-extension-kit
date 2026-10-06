import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from team_agent_contracts import (
    Citation,
    SharedMemoryCreateRequest,
    SkillFileManifest,
    SkillLock,
    SkillLockPackage,
)
from team_agent_runtime import (
    AgentDecision,
    AgentTurnRequest,
    AgentTurnResult,
    AgentTurnStatus,
    AgentUsage,
    CodingJobFailure,
    CodingJobInputRequest,
    CodingJobOutcome,
    CodingJobResult,
    DecisionKind,
    InMemoryCodingJobRepository,
    MockCodingExecutor,
    MockCodingHarness,
    ReusableLesson,
    RunStatus,
    UsageSource,
)
from team_agent_runtime.conversations import (
    DurableConversationRuntime,
    InMemoryConversationRepository,
)
from team_agent_runtime.diagnose_fix import DiagnoseAndFixRequest, DiagnoseAndFixWorkflow


def _lock() -> SkillLock:
    citation = Citation(repository="team/knowledge", path="adr/worker.md", revision="content-1")
    return SkillLock(
        catalog_revision="content-1",
        project="platform",
        packages=[
            SkillLockPackage(
                package_id=f"sha256:{'a' * 64}",
                name="diagnose-and-fix",
                description="Diagnose and fix.",
                version="1",
                source_revision="content-1",
                files=[SkillFileManifest(path="SKILL.md", sha256="b" * 64, size=1)],
                citation=citation,
            )
        ],
    )


class _Runtime:
    def __init__(self) -> None:
        self.requests: list[AgentTurnRequest] = []

    async def execute(self, request: AgentTurnRequest) -> AgentTurnResult:
        self.requests.append(request)
        return AgentTurnResult(
            run_id=request.run_id,
            status=AgentTurnStatus.COMPLETED,
            text="Planned." if request.job_completion_context is None else "Result reported.",
            canonical_knowledge_citations=[
                Citation(repository="team/knowledge", path="adr/worker.md", revision="content-1")
            ],
            supplemental_memory_citations=[],
            content_revision="content-1",
            selected_skill_lock=_lock(),
            decisions=[
                AgentDecision(
                    kind=DecisionKind.SOURCE_PRECEDENCE,
                    name="canonical_knowledge_over_memory",
                    detail="Git is canonical.",
                )
            ],
            usage=AgentUsage(source=UsageSource.SYNTHETIC),
            failures=[],
        )


class _MemoryWriter:
    def __init__(self) -> None:
        self.keys: list[str] = []
        self.requests: list[SharedMemoryCreateRequest] = []

    async def create_memory(
        self, request: SharedMemoryCreateRequest, *, idempotency_key: str
    ) -> str:
        self.keys.append(idempotency_key)
        self.requests.append(request)
        return "memory-1"


class _CrashOnceRepository(InMemoryCodingJobRepository):
    def __init__(self) -> None:
        super().__init__()
        self.crash = True

    async def finish_consumption(self, *args: object, **kwargs: object):  # type: ignore[no-untyped-def]
        if self.crash:
            self.crash = False
            raise RuntimeError("crash before final transaction")
        return await super().finish_consumption(*args, **kwargs)  # type: ignore[arg-type]


def test_fixed_workflow_replays_completion_effects_after_consumer_restart() -> None:
    async def check() -> None:
        current = datetime(2026, 1, 1, tzinfo=UTC)

        def clock() -> datetime:
            return current

        inner = _Runtime()
        jobs = _CrashOnceRepository()
        memory = _MemoryWriter()
        workflow = DiagnoseAndFixWorkflow(
            DurableConversationRuntime(inner, InMemoryConversationRepository(), clock=clock),
            jobs,
            memory_writer=memory,
            clock=clock,
        )
        started = await workflow.start(
            DiagnoseAndFixRequest(
                workflow_run_id="workflow-1",
                planning_turn_id="plan-1",
                conversation_id="conversation-1",
                project="platform",
                repository="team/service",
                repository_revision="commit-1",
                objective="Repair the worker.",
                deadline_at=current + timedelta(hours=1),
            )
        )
        result = CodingJobResult(
            job_id=started.job.job_id,
            outcome=CodingJobOutcome.FIXED,
            summary="Worker fixed.",
            changed_paths=["src/worker.py"],
            checks=["pytest"],
            reusable_lesson=ReusableLesson(title="Worker recovery", body="Restart safely."),
        )
        await MockCodingExecutor(
            jobs, MockCodingHarness(result), worker_id="mock", clock=clock
        ).execute_one()

        with pytest.raises(RuntimeError, match="crash before final"):
            await workflow.consume_one()
        current += timedelta(seconds=61)
        completed = await workflow.consume_one()

        assert completed is not None
        assert completed.run.status == RunStatus.COMPLETED
        assert completed.run.memory_id == "memory-1"
        assert completed.run.publication_outcome is not None
        assert (
            completed.run.publication_outcome.reference == f"mock://draft-pr/{started.job.job_id}"
        )
        assert completed.job.consumed_by_turn_id == completed.run.resume_turn_id
        assert len(inner.requests) == 2
        assert inner.requests[-1].job_completion_context is not None
        assert inner.requests[-1].job_completion_context.pinned_content_revision == "content-1"
        assert len(memory.keys) == 2 and memory.keys[0] == memory.keys[1]
        assert memory.requests[0].provenance == Citation(
            repository="team/knowledge", path="adr/worker.md", revision="content-1"
        )

    asyncio.run(check())


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        ("cancelled", RunStatus.CANCELLED),
        ("failed", RunStatus.FAILED),
        ("timed_out", RunStatus.TIMED_OUT),
        ("needs_input", RunStatus.NEEDS_INPUT),
        ("no_fix_found", RunStatus.COMPLETED),
        ("unsafe_to_proceed", RunStatus.COMPLETED),
    ],
)
def test_terminal_job_is_projected_once(case: str, expected: RunStatus) -> None:
    async def check() -> None:
        current = datetime(2026, 1, 1, tzinfo=UTC)
        inner = _Runtime()
        jobs = InMemoryCodingJobRepository()
        workflow = DiagnoseAndFixWorkflow(
            DurableConversationRuntime(
                inner, InMemoryConversationRepository(), clock=lambda: current
            ),
            jobs,
            clock=lambda: current,
        )
        started = await workflow.start(
            DiagnoseAndFixRequest(
                workflow_run_id="workflow-terminal",
                planning_turn_id="plan-terminal",
                conversation_id="conversation-terminal",
                project="platform",
                repository="team/service",
                repository_revision="commit-1",
                objective="Repair the worker.",
                deadline_at=current + timedelta(hours=1),
            )
        )
        if case == "cancelled":
            await jobs.cancel(started.job.job_id, current)
        elif case == "timed_out":
            current += timedelta(hours=2)
            await jobs.reconcile(current)
        else:
            claim = await jobs.claim("mock", current, current + timedelta(minutes=1))
            assert claim is not None
            if case == "failed":
                await jobs.fail(
                    claim,
                    CodingJobFailure(code="runner_failed", message="Runner failed."),
                    current,
                )
            elif case == "needs_input":
                await jobs.request_input(
                    claim,
                    CodingJobInputRequest(question="Which service should be changed?"),
                    current,
                )
            else:
                await jobs.complete(
                    claim,
                    CodingJobResult(
                        job_id=started.job.job_id,
                        outcome=CodingJobOutcome(case),
                        summary="No safe change was produced.",
                    ),
                    current,
                )
        consumed = await workflow.consume_one()
        assert consumed is not None and consumed.run.status == expected
        assert await workflow.consume_one() is None
        assert inner.requests[-1].job_completion_context is not None
        assert inner.requests[-1].job_completion_context.status == expected.value
        if case == "needs_input":
            assert (
                inner.requests[-1].job_completion_context.input_question
                == "Which service should be changed?"
            )
        if case == "failed":
            assert inner.requests[-1].job_completion_context.failure == "Runner failed."
        if case in {"no_fix_found", "unsafe_to_proceed"}:
            assert inner.requests[-1].job_completion_context.outcome == case
            assert consumed.run.publication_outcome is not None
            assert consumed.run.publication_outcome.status == "not_created"

    asyncio.run(check())


def test_stale_supplemental_lesson_is_skipped_without_blocking_completion() -> None:
    async def check() -> None:
        current = datetime(2026, 1, 1, tzinfo=UTC)
        inner = _Runtime()
        jobs = InMemoryCodingJobRepository()
        memory = _MemoryWriter()
        workflow = DiagnoseAndFixWorkflow(
            DurableConversationRuntime(
                inner, InMemoryConversationRepository(), clock=lambda: current
            ),
            jobs,
            memory_writer=memory,
            clock=lambda: current,
        )
        started = await workflow.start(
            DiagnoseAndFixRequest(
                workflow_run_id="workflow-stale-lesson",
                planning_turn_id="plan-stale-lesson",
                conversation_id="conversation-stale-lesson",
                project="platform",
                repository="team/service",
                repository_revision="commit-1",
                objective="Repair the worker.",
                deadline_at=current + timedelta(hours=1),
            )
        )
        await MockCodingExecutor(
            jobs,
            MockCodingHarness(
                CodingJobResult(
                    job_id=started.job.job_id,
                    outcome=CodingJobOutcome.FIXED,
                    summary="Fixed.",
                    reusable_lesson=ReusableLesson(title="Lesson", body="Body"),
                )
            ),
            worker_id="mock",
            clock=lambda: current,
        ).execute_one()
        current += timedelta(days=91)

        consumed = await workflow.consume_one()

        assert consumed is not None and consumed.run.status == RunStatus.COMPLETED
        assert consumed.run.memory_id is None
        assert memory.keys == []

    asyncio.run(check())

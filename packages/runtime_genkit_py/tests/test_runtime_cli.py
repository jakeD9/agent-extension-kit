import asyncio
import io

from team_agent_runtime import (
    AgentDecision,
    AgentFailure,
    AgentTurnRequest,
    AgentTurnResult,
    AgentTurnStatus,
    AgentUsage,
    DecisionKind,
    JobCompletionContext,
    UsageSource,
)
from team_agent_runtime_genkit.cli import MAX_REQUEST_BYTES, run_cli


class _Runtime:
    async def execute(self, request: AgentTurnRequest) -> AgentTurnResult:
        return AgentTurnResult(
            run_id=request.run_id,
            status=AgentTurnStatus.FAILED,
            text="",
            canonical_knowledge_citations=[],
            supplemental_memory_citations=[],
            decisions=[
                AgentDecision(
                    kind=DecisionKind.SOURCE_PRECEDENCE,
                    name="canonical_knowledge_over_memory",
                    detail="Git wins.",
                )
            ],
            usage=AgentUsage(source=UsageSource.SYNTHETIC),
            failures=[AgentFailure(code="fixture_failure", message="Fixture failed.")],
        )


def test_cli_rejects_oversized_stdin_with_bounded_json_error() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()

    code = asyncio.run(
        run_cli(
            ["invoke"],
            stdin=io.BytesIO(b"x" * (MAX_REQUEST_BYTES + 1)),
            stdout=stdout,
            stderr=stderr,
            runtime_factory=lambda: _Runtime(),
        )
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert stderr.getvalue() == (
        '{"error":{"code":"request_too_large","message":"Request exceeds the size limit."}}\n'
    )


def test_cli_emits_exactly_one_runtime_result_and_nonzero_for_explicit_failure() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    request = AgentTurnRequest(
        run_id="run-1",
        project="platform",
        objective="answer",
        skill_names=["incident-guide"],
    )

    code = asyncio.run(
        run_cli(
            ["invoke"],
            stdin=io.BytesIO(request.model_dump_json().encode()),
            stdout=stdout,
            stderr=stderr,
            runtime_factory=lambda: _Runtime(),
        )
    )

    assert code == 1
    assert stdout.getvalue().count("\n") == 1
    assert '"status":"failed"' in stdout.getvalue()
    assert stderr.getvalue() == ""


def test_stateless_cli_rejects_application_owned_job_context() -> None:
    stdout = io.StringIO()
    stderr = io.StringIO()
    request = AgentTurnRequest(
        run_id="run-1",
        project="platform",
        objective="answer",
        skill_names=["incident-guide"],
        job_completion_context=JobCompletionContext(
            job_id="job-1",
            attempt=1,
            status="cancelled",
            repository="team/service",
            repository_revision="commit-1",
            pinned_content_revision="content-1",
        ),
    )

    code = asyncio.run(
        run_cli(
            ["invoke"],
            stdin=io.BytesIO(request.model_dump_json().encode()),
            stdout=stdout,
            stderr=stderr,
            runtime_factory=lambda: _Runtime(),
        )
    )

    assert code == 2
    assert stdout.getvalue() == ""
    assert '"code":"invalid_request"' in stderr.getvalue()

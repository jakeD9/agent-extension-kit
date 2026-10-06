"""Provider-neutral contract for one stateless coordinator turn."""

from __future__ import annotations

import re
from enum import StrEnum
from pathlib import PurePosixPath
from typing import Literal, Protocol

from pydantic import BaseModel, ConfigDict, Field, model_validator
from team_agent_contracts import SKILL_NAME_PATTERN, Citation, SkillLock

from team_agent_runtime.jobs import (
    CodingHarness,
    CodingJobClaim,
    CodingJobConflictError,
    CodingJobFailure,
    CodingJobInputRequest,
    CodingJobNotFoundError,
    CodingJobOutcome,
    CodingJobRecord,
    CodingJobRepository,
    CodingJobRequest,
    CodingJobResult,
    CodingJobStatus,
    CodingRunSnapshot,
    InMemoryCodingJobRepository,
    MockCodingExecutor,
    MockCodingHarness,
    MockPublicationOutcome,
    ReusableLesson,
    RunConsumptionClaim,
    RunRecord,
    RunStatus,
    StaleCodingJobClaimError,
    StaleRunConsumptionClaimError,
    coding_job_completion_fingerprint,
    coding_job_request_fingerprint,
    coding_job_result_fingerprint,
)

MAX_CONVERSATION_CONTEXT_CHARS = 48_000


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AgentTurnStatus(StrEnum):
    COMPLETED = "completed"
    FAILED = "failed"


class UsageSource(StrEnum):
    OBSERVED = "observed"
    SYNTHETIC = "synthetic"


class DecisionKind(StrEnum):
    SKILL_USE = "skill_use"
    TOOL_USE = "tool_use"
    SOURCE_PRECEDENCE = "source_precedence"


class ConversationTurnContext(_Contract):
    """One completed prior exchange included in a bounded model context."""

    sequence: int = Field(ge=1)
    user: str = Field(min_length=1, max_length=20_000)
    assistant: str = Field(min_length=1, max_length=20_000)


class ConversationContext(_Contract):
    """Application-owned summary and recent turns supplied to a stateless runtime."""

    summary: str = Field(default="", max_length=12_000)
    recent_turns: list[ConversationTurnContext] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def ordered_unique_turns(self) -> ConversationContext:
        sequences = [turn.sequence for turn in self.recent_turns]
        if sequences != sorted(set(sequences)):
            raise ValueError("recent_turns must have unique ascending sequence numbers")
        total_chars = len(self.summary) + sum(
            len(turn.user) + len(turn.assistant) for turn in self.recent_turns
        )
        if total_chars > MAX_CONVERSATION_CONTEXT_CHARS:
            raise ValueError("conversation context exceeds the aggregate character limit")
        return self


class JobCompletionContext(_Contract):
    """Bounded application data supplied when a coding job resumes a conversation."""

    job_id: str = Field(min_length=1, max_length=160)
    attempt: int = Field(ge=0)
    status: Literal["completed", "failed", "timed_out", "cancelled", "needs_input"]
    outcome: Literal["fixed", "no_fix_found", "unsafe_to_proceed"] | None = None
    summary: str | None = Field(default=None, max_length=20_000)
    changed_paths: list[str] = Field(default_factory=list, max_length=2_000)
    checks: list[str] = Field(default_factory=list, max_length=2_000)
    failure: str | None = Field(default=None, max_length=500)
    input_question: str | None = Field(default=None, max_length=2_000)
    mock_draft_pr_reference: str | None = Field(default=None, max_length=500)
    repository: str = Field(min_length=1, max_length=500)
    repository_revision: str = Field(min_length=1, max_length=256)
    pinned_content_revision: str = Field(min_length=1, max_length=256)

    @model_validator(mode="after")
    def lifecycle_payload_is_consistent(self) -> JobCompletionContext:
        if any(
            not value
            or len(value) > 2_000
            or "\\" in value
            or "\0" in value
            or PurePosixPath(value).is_absolute()
            or ".." in PurePosixPath(value).parts
            for value in self.changed_paths
        ):
            raise ValueError("changed_paths must be bounded repository-relative paths")
        if any(not value or len(value) > 2_000 for value in self.checks):
            raise ValueError("checks must be non-empty and bounded")
        if self.status == "completed":
            if self.outcome is None or self.summary is None or self.failure or self.input_question:
                raise ValueError("completed context requires only a successful result")
        elif self.status == "failed":
            if (
                self.failure is None
                or self.outcome is not None
                or self.input_question
                or self.summary is not None
                or self.changed_paths
                or self.checks
            ):
                raise ValueError("failed context requires only a failure")
        elif self.status == "needs_input":
            if (
                self.input_question is None
                or self.outcome is not None
                or self.failure
                or self.summary is not None
                or self.changed_paths
                or self.checks
            ):
                raise ValueError("needs_input context requires only a question")
        elif (
            self.outcome is not None
            or self.failure
            or self.input_question
            or self.summary is not None
            or self.changed_paths
            or self.checks
        ):
            raise ValueError("cancelled and timed-out context cannot contain result details")
        if (self.mock_draft_pr_reference is not None) != (
            self.status == "completed" and self.outcome == "fixed"
        ):
            raise ValueError("only a fixed completion requires a mock draft reference")
        return self


class AgentTurnRequest(_Contract):
    schema_version: Literal["1"] = "1"
    run_id: str = Field(min_length=1, max_length=128)
    project: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20_000)
    skill_names: list[str] = Field(min_length=1, max_length=20)
    conversation_context: ConversationContext | None = None
    job_completion_context: JobCompletionContext | None = None

    @model_validator(mode="after")
    def unique_valid_skills(self) -> AgentTurnRequest:
        if len(set(self.skill_names)) != len(self.skill_names) or any(
            re.fullmatch(SKILL_NAME_PATTERN, name) is None for name in self.skill_names
        ):
            raise ValueError("skill_names must be unique kebab-case names")
        return self


class AgentDecision(_Contract):
    kind: DecisionKind
    name: str = Field(min_length=1, max_length=128)
    detail: str = Field(min_length=1, max_length=2_000)


class AgentUsage(_Contract):
    source: UsageSource
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cached_content_tokens: int = Field(default=0, ge=0)
    thoughts_tokens: int = Field(default=0, ge=0)


class AgentFailure(_Contract):
    code: str = Field(min_length=1, max_length=128)
    message: str = Field(min_length=1, max_length=500)
    retriable: bool = False


class AgentTurnResult(_Contract):
    schema_version: Literal["1"] = "1"
    run_id: str
    status: AgentTurnStatus
    text: str
    canonical_knowledge_citations: list[Citation]
    supplemental_memory_citations: list[Citation]
    content_revision: str | None = Field(default=None, max_length=256)
    selected_skill_lock: SkillLock | None = None
    decisions: list[AgentDecision]
    usage: AgentUsage
    failures: list[AgentFailure]

    @model_validator(mode="after")
    def consistent_status(self) -> AgentTurnResult:
        if self.status == AgentTurnStatus.COMPLETED:
            if (
                self.failures
                or self.selected_skill_lock is None
                or self.content_revision is None
                or not self.canonical_knowledge_citations
                or self.selected_skill_lock.catalog_revision != self.content_revision
                or any(
                    citation.revision != self.content_revision
                    for citation in self.canonical_knowledge_citations
                )
                or not any(
                    decision.kind == DecisionKind.SOURCE_PRECEDENCE for decision in self.decisions
                )
            ):
                raise ValueError(
                    "completed results require canonical revision evidence and no failures"
                )
        elif not self.failures:
            raise ValueError("failed results require a failure")
        return self


class AgentRuntime(Protocol):
    async def execute(self, request: AgentTurnRequest) -> AgentTurnResult: ...


__all__ = [
    "MAX_CONVERSATION_CONTEXT_CHARS",
    "AgentDecision",
    "AgentFailure",
    "AgentRuntime",
    "AgentTurnRequest",
    "AgentTurnResult",
    "AgentTurnStatus",
    "AgentUsage",
    "CodingHarness",
    "CodingJobClaim",
    "CodingJobConflictError",
    "CodingJobFailure",
    "CodingJobInputRequest",
    "CodingJobNotFoundError",
    "CodingJobOutcome",
    "CodingJobRecord",
    "CodingJobRepository",
    "CodingJobRequest",
    "CodingJobResult",
    "CodingJobStatus",
    "CodingRunSnapshot",
    "ConversationContext",
    "ConversationTurnContext",
    "DecisionKind",
    "InMemoryCodingJobRepository",
    "JobCompletionContext",
    "MockCodingExecutor",
    "MockCodingHarness",
    "MockPublicationOutcome",
    "ReusableLesson",
    "RunConsumptionClaim",
    "RunRecord",
    "RunStatus",
    "StaleCodingJobClaimError",
    "StaleRunConsumptionClaimError",
    "UsageSource",
    "coding_job_completion_fingerprint",
    "coding_job_request_fingerprint",
    "coding_job_result_fingerprint",
]

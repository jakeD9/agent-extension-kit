"""Provider-neutral contract for one stateless coordinator turn."""

from __future__ import annotations

import re
from enum import StrEnum
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
    RunRecord,
    RunStatus,
    StaleCodingJobClaimError,
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


class AgentTurnRequest(_Contract):
    schema_version: Literal["1"] = "1"
    run_id: str = Field(min_length=1, max_length=128)
    project: str = Field(min_length=1, max_length=200)
    objective: str = Field(min_length=1, max_length=20_000)
    skill_names: list[str] = Field(min_length=1, max_length=20)
    conversation_context: ConversationContext | None = None

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
    "MockCodingExecutor",
    "MockCodingHarness",
    "RunRecord",
    "RunStatus",
    "StaleCodingJobClaimError",
    "UsageSource",
    "coding_job_request_fingerprint",
    "coding_job_result_fingerprint",
]

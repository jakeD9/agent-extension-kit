"""Durable, application-owned conversation orchestration around a stateless runtime."""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from typing import Any, Protocol

from team_agent_runtime import (
    AgentFailure,
    AgentRuntime,
    AgentTurnRequest,
    AgentTurnResult,
    AgentTurnStatus,
    AgentUsage,
    ConversationContext,
    ConversationTurnContext,
    UsageSource,
)

Document = dict[str, Any]
MAX_RECENT_TURNS = 6
MAX_SUMMARY_CHARS = 12_000
MAX_RECENT_CONTEXT_CHARS = 36_000
MAX_CONTEXT_TEXT_CHARS = 18_000
DEFAULT_CLAIM_TTL = timedelta(seconds=60)
MAX_TURN_DOCUMENT_BYTES = 8 * 1024 * 1024


class ConversationBusyError(RuntimeError):
    """Another turn has an unexpired claim for this conversation."""


class ConversationRequestConflictError(RuntimeError):
    """A run ID was reused with a different request."""


class StaleConversationClaimError(RuntimeError):
    """A superseded worker attempted to persist a result."""


@dataclass(frozen=True)
class ConversationClaim:
    conversation_id: str
    run_id: str
    generation: int
    request_fingerprint: str
    context: ConversationContext
    replay_result: AgentTurnResult | None = None


class ConversationRepository(Protocol):
    async def claim(
        self,
        conversation_id: str,
        request: AgentTurnRequest,
        request_fingerprint: str,
        now: datetime,
        expires_at: datetime,
        *,
        retry_retriable_failure: bool = False,
    ) -> ConversationClaim: ...

    async def complete(
        self, claim: ConversationClaim, result: AgentTurnResult, now: datetime
    ) -> None: ...


def request_fingerprint(request: AgentTurnRequest) -> str:
    """Identify caller-supplied turn input independently of injected history."""

    payload = request.model_copy(update={"conversation_context": None}).model_dump(
        mode="json", exclude={"conversation_context"}
    )
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":")).encode()
    return f"sha256:{sha256(encoded).hexdigest()}"


def _failed_result(run_id: str, code: str, message: str, *, retriable: bool) -> AgentTurnResult:
    return AgentTurnResult(
        run_id=run_id,
        status=AgentTurnStatus.FAILED,
        text="",
        canonical_knowledge_citations=[],
        supplemental_memory_citations=[],
        decisions=[],
        usage=AgentUsage(source=UsageSource.SYNTHETIC),
        failures=[AgentFailure(code=code, message=message, retriable=retriable)],
    )


def _bounded_text(value: str) -> str:
    if len(value) <= MAX_CONTEXT_TEXT_CHARS:
        return value
    return f"{value[: MAX_CONTEXT_TEXT_CHARS - 1]}…"


def _summary_entry(turn: Document) -> str:
    request = AgentTurnRequest.model_validate(turn["request"])
    result = AgentTurnResult.model_validate(turn["result"])
    return (
        f"Turn {turn['sequence']}\n"
        f"User: {_bounded_text(request.objective)}\n"
        f"Assistant: {_bounded_text(result.text)}"
    )


def extend_summary(summary: str, turns: list[Document]) -> str:
    """Append exact ordered turn extracts and retain a deterministic bounded tail."""

    additions = "\n\n".join(_summary_entry(turn) for turn in turns)
    combined = "\n\n".join(part for part in (summary, additions) if part)
    if len(combined) <= MAX_SUMMARY_CHARS:
        return combined
    marker = "[Earlier compacted conversation omitted]\n"
    return marker + combined[-(MAX_SUMMARY_CHARS - len(marker)) :]


def _context_from(conversation: Document, turns: list[Document]) -> ConversationContext:
    return ConversationContext(
        summary=str(conversation.get("summary", "")),
        recent_turns=[
            ConversationTurnContext(
                sequence=int(turn["sequence"]),
                user=_bounded_text(AgentTurnRequest.model_validate(turn["request"]).objective),
                assistant=_bounded_text(AgentTurnResult.model_validate(turn["result"]).text),
            )
            for turn in turns
        ],
    )


class InMemoryConversationRepository:
    """Behavioral reference store used for local execution and contract tests."""

    def __init__(self) -> None:
        self._conversations: dict[str, Document] = {}
        self._turns: dict[tuple[str, str], Document] = {}
        self._mutex = asyncio.Lock()

    async def claim(
        self,
        conversation_id: str,
        request: AgentTurnRequest,
        request_fingerprint: str,
        now: datetime,
        expires_at: datetime,
        *,
        retry_retriable_failure: bool = False,
    ) -> ConversationClaim:
        async with self._mutex:
            key = (conversation_id, request.run_id)
            existing_turn = self._turns.get(key)
            if existing_turn is not None:
                if existing_turn["request_fingerprint"] != request_fingerprint:
                    raise ConversationRequestConflictError(
                        "run_id was already used for a different request"
                    )
                if existing_turn["status"] in {
                    AgentTurnStatus.COMPLETED.value,
                    AgentTurnStatus.FAILED.value,
                }:
                    stored_result = AgentTurnResult.model_validate(existing_turn["result"])
                    if (
                        stored_result.status == AgentTurnStatus.COMPLETED
                        or not retry_retriable_failure
                        or not all(failure.retriable for failure in stored_result.failures)
                    ):
                        return ConversationClaim(
                            conversation_id=conversation_id,
                            run_id=request.run_id,
                            generation=int(existing_turn["claim_generation"]),
                            request_fingerprint=request_fingerprint,
                            context=ConversationContext(),
                            replay_result=stored_result,
                        )

            conversation = self._conversations.setdefault(
                conversation_id,
                {
                    "_id": conversation_id,
                    "schema_version": "1",
                    "project": request.project,
                    "summary": "",
                    "summary_through_sequence": 0,
                    "active_turn_id": None,
                    "claim_generation": 0,
                    "claim_expires_at": None,
                    "created_at": now,
                    "updated_at": now,
                },
            )
            if conversation["project"] != request.project:
                raise ConversationRequestConflictError(
                    "conversation belongs to a different project"
                )
            active_turn_id = conversation["active_turn_id"]
            active_expiry = conversation["claim_expires_at"]
            if (
                active_turn_id is not None
                and active_turn_id != request.run_id
                and active_expiry is not None
                and active_expiry > now
            ):
                raise ConversationBusyError("conversation already has an active turn")

            if active_turn_id is not None and active_turn_id != request.run_id:
                expired = self._turns.get((conversation_id, str(active_turn_id)))
                if expired is not None and expired["status"] == "active":
                    failure = _failed_result(
                        str(active_turn_id),
                        "claim_expired",
                        "The turn claim expired before completion.",
                        retriable=True,
                    )
                    expired.update(
                        {
                            "status": AgentTurnStatus.FAILED.value,
                            "result": failure.model_dump(mode="json"),
                            "completed_at": now,
                        }
                    )

            conversation["claim_generation"] = int(conversation["claim_generation"]) + 1
            generation = int(conversation["claim_generation"])
            conversation.update(
                {
                    "active_turn_id": request.run_id,
                    "claim_expires_at": expires_at,
                    "updated_at": now,
                }
            )
            turn = existing_turn or {
                "_id": f"{conversation_id}:{request.run_id}",
                "schema_version": "1",
                "conversation_id": conversation_id,
                "run_id": request.run_id,
                "sequence": generation,
                "project": request.project,
                "request_fingerprint": request_fingerprint,
                "request": request.model_copy(update={"conversation_context": None}).model_dump(
                    mode="json"
                ),
                "result": None,
                "status": "active",
                "created_at": now,
                "completed_at": None,
            }
            turn.update(
                {
                    "sequence": generation,
                    "claim_generation": generation,
                    "claim_expires_at": expires_at,
                    "status": "active",
                    "result": None,
                    "completed_at": None,
                }
            )
            self._turns[key] = turn
            recent = self._recent_completed(conversation_id, conversation)[-MAX_RECENT_TURNS:]
            return ConversationClaim(
                conversation_id=conversation_id,
                run_id=request.run_id,
                generation=generation,
                request_fingerprint=request_fingerprint,
                context=_context_from(conversation, recent),
            )

    async def complete(
        self, claim: ConversationClaim, result: AgentTurnResult, now: datetime
    ) -> None:
        async with self._mutex:
            conversation = self._conversations[claim.conversation_id]
            if (
                conversation["active_turn_id"] != claim.run_id
                or conversation["claim_generation"] != claim.generation
            ):
                raise StaleConversationClaimError("turn claim has been superseded")
            turn = self._turns[(claim.conversation_id, claim.run_id)]
            turn.update(
                {
                    "status": result.status.value,
                    "result": result.model_dump(mode="json"),
                    "completed_at": now,
                }
            )
            conversation.update(
                {
                    "active_turn_id": None,
                    "claim_expires_at": None,
                    "updated_at": now,
                }
            )
            self._compact(claim.conversation_id, conversation)

    def _recent_completed(self, conversation_id: str, conversation: Document) -> list[Document]:
        turns = sorted(
            (
                turn
                for (stored_conversation_id, _run_id), turn in self._turns.items()
                if stored_conversation_id == conversation_id
                and turn["status"] == AgentTurnStatus.COMPLETED.value
                and turn["sequence"] > conversation["summary_through_sequence"]
            ),
            key=lambda turn: int(turn["sequence"]),
        )
        return turns

    def _compact(self, conversation_id: str, conversation: Document) -> None:
        turns = self._recent_completed(conversation_id, conversation)
        while len(turns) > 1 and (
            len(turns) > MAX_RECENT_TURNS
            or sum(
                len(_bounded_text(AgentTurnRequest.model_validate(turn["request"]).objective))
                + len(_bounded_text(AgentTurnResult.model_validate(turn["result"]).text))
                for turn in turns
            )
            > MAX_RECENT_CONTEXT_CHARS
        ):
            compacted = turns.pop(0)
            conversation["summary"] = extend_summary(str(conversation["summary"]), [compacted])
            conversation["summary_through_sequence"] = compacted["sequence"]

    async def read_turn(self, conversation_id: str, run_id: str) -> Document | None:
        async with self._mutex:
            turn = self._turns.get((conversation_id, run_id))
            return None if turn is None else dict(turn)

    async def read_conversation(self, conversation_id: str) -> Document | None:
        async with self._mutex:
            conversation = self._conversations.get(conversation_id)
            return None if conversation is None else dict(conversation)


class DurableConversationRuntime:
    """Serialize each conversation and inject only bounded application history."""

    def __init__(
        self,
        runtime: AgentRuntime,
        repository: ConversationRepository,
        *,
        clock: Callable[[], datetime] | None = None,
        claim_ttl: timedelta = DEFAULT_CLAIM_TTL,
    ) -> None:
        self._runtime = runtime
        self._repository = repository
        self._clock = clock or (lambda: datetime.now(UTC))
        self._claim_ttl = claim_ttl
        self._locks: dict[str, asyncio.Lock] = {}

    async def execute(
        self,
        conversation_id: str,
        request: AgentTurnRequest,
        *,
        retry_retriable_failure: bool = False,
    ) -> AgentTurnResult:
        if request.conversation_context is not None:
            raise ConversationRequestConflictError(
                "conversation_context is supplied by the durable runtime"
            )
        lock = self._locks.setdefault(conversation_id, asyncio.Lock())
        async with lock:
            now = self._clock()
            fingerprint = request_fingerprint(request)
            claim = await self._repository.claim(
                conversation_id,
                request,
                fingerprint,
                now,
                now + self._claim_ttl,
                retry_retriable_failure=retry_retriable_failure,
            )
            if claim.replay_result is not None:
                return claim.replay_result
            contextual_request = request.model_copy(update={"conversation_context": claim.context})
            try:
                result = await self._runtime.execute(contextual_request)
            except asyncio.CancelledError:
                cancelled = _failed_result(
                    request.run_id,
                    "turn_cancelled",
                    "The conversation turn was cancelled before completion.",
                    retriable=True,
                )
                persistence = asyncio.create_task(
                    self._repository.complete(claim, cancelled, self._clock())
                )
                try:
                    await asyncio.shield(persistence)
                except asyncio.CancelledError:
                    await asyncio.gather(persistence, return_exceptions=True)
                except Exception:
                    pass
                raise
            except Exception:
                result = _failed_result(
                    request.run_id,
                    "runtime_failed",
                    "The conversation runtime could not complete the turn.",
                    retriable=True,
                )
            if result.run_id != request.run_id:
                result = _failed_result(
                    request.run_id,
                    "runtime_result_mismatch",
                    "The runtime result did not match the requested run.",
                    retriable=False,
                )
            persisted_payload = {
                "request": request.model_dump(mode="json"),
                "result": result.model_dump(mode="json"),
            }
            if (
                len(
                    json.dumps(
                        persisted_payload, separators=(",", ":"), ensure_ascii=False
                    ).encode()
                )
                > MAX_TURN_DOCUMENT_BYTES
            ):
                result = _failed_result(
                    request.run_id,
                    "turn_result_too_large",
                    "The turn result exceeds the durable storage size limit.",
                    retriable=False,
                )
            await self._repository.complete(claim, result, self._clock())
            return result


__all__ = [
    "DEFAULT_CLAIM_TTL",
    "MAX_CONTEXT_TEXT_CHARS",
    "MAX_RECENT_CONTEXT_CHARS",
    "MAX_RECENT_TURNS",
    "MAX_SUMMARY_CHARS",
    "MAX_TURN_DOCUMENT_BYTES",
    "ConversationBusyError",
    "ConversationClaim",
    "ConversationRepository",
    "ConversationRequestConflictError",
    "DurableConversationRuntime",
    "InMemoryConversationRepository",
    "StaleConversationClaimError",
    "extend_summary",
    "request_fingerprint",
]

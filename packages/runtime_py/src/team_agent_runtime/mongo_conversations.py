"""MongoDB persistence for application-owned durable conversations."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any, cast

from pymongo import ASCENDING, ReturnDocument
from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import DuplicateKeyError

from team_agent_runtime import AgentTurnRequest, AgentTurnResult, AgentTurnStatus
from team_agent_runtime.conversations import (
    MAX_RECENT_CONTEXT_CHARS,
    MAX_RECENT_TURNS,
    ConversationBusyError,
    ConversationClaim,
    ConversationRequestConflictError,
    Document,
    StaleConversationClaimError,
    _bounded_text,
    _context_from,
    _failed_result,
    extend_summary,
)


def _object_schema(required: list[str], properties: Document) -> Document:
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", *required],
            "additionalProperties": False,
            "properties": {"_id": {"bsonType": "string"}, **properties},
        }
    }


RUNTIME_COLLECTION_VALIDATORS: dict[str, Document] = {
    "conversations": _object_schema(
        [
            "schema_version",
            "project",
            "summary",
            "summary_through_sequence",
            "active_turn_id",
            "claim_generation",
            "claim_expires_at",
            "created_at",
            "updated_at",
        ],
        {
            "schema_version": {"enum": ["1"]},
            "project": {"bsonType": "string"},
            "summary": {"bsonType": "string", "maxLength": 12_000},
            "summary_through_sequence": {"bsonType": "int", "minimum": 0},
            "active_turn_id": {"bsonType": ["string", "null"]},
            "claim_generation": {"bsonType": "int", "minimum": 0},
            "claim_expires_at": {"bsonType": ["date", "null"]},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
        },
    ),
    "conversation_turns": _object_schema(
        [
            "schema_version",
            "conversation_id",
            "run_id",
            "sequence",
            "project",
            "request_fingerprint",
            "request",
            "result",
            "status",
            "claim_generation",
            "claim_expires_at",
            "created_at",
            "completed_at",
        ],
        {
            "schema_version": {"enum": ["1"]},
            "conversation_id": {"bsonType": "string"},
            "run_id": {"bsonType": "string"},
            "sequence": {"bsonType": "int", "minimum": 1},
            "project": {"bsonType": "string"},
            "request_fingerprint": {"bsonType": "string"},
            "request": {"bsonType": "object"},
            "result": {"bsonType": ["object", "null"]},
            "status": {"enum": ["active", "completed", "failed"]},
            "claim_generation": {"bsonType": "int", "minimum": 1},
            "claim_expires_at": {"bsonType": "date"},
            "created_at": {"bsonType": "date"},
            "completed_at": {"bsonType": ["date", "null"]},
        },
    ),
    "schema_migrations": _object_schema(
        ["schema_version", "applied_at"],
        {
            "schema_version": {"bsonType": "int"},
            "applied_at": {"bsonType": "date"},
        },
    ),
}


class MongoConversationRepository:
    """Bounded MongoDB history reads plus generation-fenced turn persistence."""

    def __init__(self, database: AsyncDatabase[Document]) -> None:
        self._database = database

    async def migrate(self, now: datetime | None = None) -> None:
        applied_at = now or datetime.now(UTC)
        existing = set(await self._database.list_collection_names())
        for name, validator in RUNTIME_COLLECTION_VALIDATORS.items():
            if name not in existing:
                await self._database.create_collection(
                    name,
                    validator=validator,
                    validationLevel="strict",
                    validationAction="error",
                )
            else:
                await self._database.command(
                    {
                        "collMod": name,
                        "validator": validator,
                        "validationLevel": "strict",
                        "validationAction": "error",
                    }
                )
        await self._database["conversations"].create_index(
            [("claim_expires_at", ASCENDING)], name="claim_expiration"
        )
        await self._database["conversation_turns"].create_index(
            [("conversation_id", ASCENDING), ("sequence", ASCENDING)],
            name="conversation_sequence_unique",
            unique=True,
        )
        await self._database["conversation_turns"].create_index(
            [
                ("conversation_id", ASCENDING),
                ("status", ASCENDING),
                ("sequence", ASCENDING),
            ],
            name="conversation_status_sequence",
        )
        await self._database["conversation_turns"].create_index(
            [("conversation_id", ASCENDING), ("run_id", ASCENDING)],
            name="conversation_run_unique",
            unique=True,
        )
        await self._database["schema_migrations"].update_one(
            {"_id": "runtime-schema-v1"},
            {"$setOnInsert": {"schema_version": 1, "applied_at": applied_at}},
            upsert=True,
        )

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
        turns = self._database["conversation_turns"]
        turn_id = f"{conversation_id}:{request.run_id}"
        conversations = self._database["conversations"]
        stored_request = request.model_copy(update={"conversation_context": None}).model_dump(
            mode="json"
        )

        async def acquire(active_session: Any) -> ConversationClaim:
            existing_turn = await turns.find_one({"_id": turn_id}, session=active_session)
            if existing_turn is not None:
                if existing_turn.get("request_fingerprint") != request_fingerprint:
                    raise ConversationRequestConflictError(
                        "run_id was already used for a different request"
                    )
                if existing_turn.get("status") in {
                    AgentTurnStatus.COMPLETED.value,
                    AgentTurnStatus.FAILED.value,
                }:
                    stored_result = AgentTurnResult.model_validate(existing_turn["result"])
                    if (
                        stored_result.status == AgentTurnStatus.COMPLETED
                        or not retry_retriable_failure
                        or not all(failure.retriable for failure in stored_result.failures)
                    ):
                        await self._release_replayed_claim(existing_turn, now, active_session)
                        return ConversationClaim(
                            conversation_id=conversation_id,
                            run_id=request.run_id,
                            generation=int(existing_turn["claim_generation"]),
                            request_fingerprint=request_fingerprint,
                            context=_context_from({"summary": ""}, []),
                            replay_result=stored_result,
                        )

            await conversations.update_one(
                {"_id": conversation_id},
                {
                    "$setOnInsert": {
                        "schema_version": "1",
                        "project": request.project,
                        "summary": "",
                        "summary_through_sequence": 0,
                        "active_turn_id": None,
                        "claim_generation": 0,
                        "claim_expires_at": None,
                        "created_at": now,
                        "updated_at": now,
                    }
                },
                upsert=True,
                session=active_session,
            )
            before = await conversations.find_one({"_id": conversation_id}, session=active_session)
            previous_active_turn_id = None if before is None else before.get("active_turn_id")
            previous_claim_expiry = None if before is None else before.get("claim_expires_at")
            previous_generation = 0 if before is None else int(before["claim_generation"])
            conversation = await conversations.find_one_and_update(
                {
                    "_id": conversation_id,
                    "project": request.project,
                    "$or": [
                        {"active_turn_id": None},
                        {"claim_expires_at": {"$lte": now}},
                        {"active_turn_id": request.run_id},
                    ],
                },
                {
                    "$set": {
                        "active_turn_id": request.run_id,
                        "claim_expires_at": expires_at,
                        "updated_at": now,
                    },
                    "$inc": {"claim_generation": 1},
                },
                return_document=ReturnDocument.AFTER,
                session=active_session,
            )
            if conversation is None:
                observed = await conversations.find_one(
                    {"_id": conversation_id}, session=active_session
                )
                if observed is not None and observed.get("project") != request.project:
                    raise ConversationRequestConflictError(
                        "conversation belongs to a different project"
                    )
                raise ConversationBusyError("conversation already has an active turn")

            generation = int(conversation["claim_generation"])
            if (
                previous_active_turn_id not in {None, request.run_id}
                and previous_claim_expiry is not None
                and previous_claim_expiry <= now
            ):
                expired_run_id = str(previous_active_turn_id)
                failure = _failed_result(
                    expired_run_id,
                    "claim_expired",
                    "The turn claim expired before completion.",
                    retriable=True,
                )
                await turns.update_one(
                    {
                        "conversation_id": conversation_id,
                        "run_id": expired_run_id,
                        "claim_generation": previous_generation,
                        "status": "active",
                    },
                    {
                        "$set": {
                            "status": AgentTurnStatus.FAILED.value,
                            "result": failure.model_dump(mode="json"),
                            "completed_at": now,
                        }
                    },
                    session=active_session,
                )

            try:
                await turns.update_one(
                    {"_id": turn_id, "request_fingerprint": request_fingerprint},
                    {
                        "$setOnInsert": {
                            "_id": turn_id,
                            "schema_version": "1",
                            "conversation_id": conversation_id,
                            "run_id": request.run_id,
                            "project": request.project,
                            "request_fingerprint": request_fingerprint,
                            "request": stored_request,
                            "created_at": now,
                        },
                        "$set": {
                            "sequence": generation,
                            "claim_generation": generation,
                            "claim_expires_at": expires_at,
                            "status": "active",
                            "result": None,
                            "completed_at": None,
                        },
                    },
                    upsert=True,
                    session=active_session,
                )
            except DuplicateKeyError as error:
                raise ConversationRequestConflictError(
                    "run_id was already used for a different request"
                ) from error
            return ConversationClaim(
                conversation_id=conversation_id,
                run_id=request.run_id,
                generation=generation,
                request_fingerprint=request_fingerprint,
                context=_context_from({"summary": ""}, []),
            )

        async with self._database.client.start_session() as session:
            claim = cast(ConversationClaim, await session.with_transaction(acquire))

        await self._compact(conversation_id)
        if claim.replay_result is not None:
            return claim
        reloaded = await conversations.find_one({"_id": conversation_id})
        if reloaded is None:
            raise StaleConversationClaimError("claimed conversation was not found")
        recent = await self._load_recent(conversation_id, reloaded)
        return ConversationClaim(
            conversation_id=conversation_id,
            run_id=request.run_id,
            generation=claim.generation,
            request_fingerprint=request_fingerprint,
            context=_context_from(reloaded, recent),
        )

    async def complete(
        self, claim: ConversationClaim, result: AgentTurnResult, now: datetime
    ) -> None:
        conversations = self._database["conversations"]
        turns = self._database["conversation_turns"]

        async def persist(active_session: Any) -> None:
            fenced = await conversations.find_one(
                {
                    "_id": claim.conversation_id,
                    "active_turn_id": claim.run_id,
                    "claim_generation": claim.generation,
                },
                session=active_session,
            )
            if fenced is None:
                raise StaleConversationClaimError("turn claim has been superseded")
            write = await turns.update_one(
                {
                    "conversation_id": claim.conversation_id,
                    "run_id": claim.run_id,
                    "claim_generation": claim.generation,
                    "status": "active",
                },
                {
                    "$set": {
                        "status": result.status.value,
                        "result": result.model_dump(mode="json"),
                        "completed_at": now,
                    }
                },
                session=active_session,
            )
            if write.matched_count != 1:
                raise StaleConversationClaimError("active turn record was not found")
            released = await conversations.update_one(
                {
                    "_id": claim.conversation_id,
                    "active_turn_id": claim.run_id,
                    "claim_generation": claim.generation,
                },
                {
                    "$set": {
                        "active_turn_id": None,
                        "claim_expires_at": None,
                        "updated_at": now,
                    },
                },
                session=active_session,
            )
            if released.matched_count != 1:
                raise StaleConversationClaimError("turn claim changed before release")

        async with self._database.client.start_session() as session:
            await session.with_transaction(persist)
        await self._compact(claim.conversation_id)

    async def _release_replayed_claim(self, turn: Document, now: datetime, session: Any) -> None:
        await self._database["conversations"].update_one(
            {
                "_id": turn["conversation_id"],
                "active_turn_id": turn["run_id"],
                "claim_generation": turn["claim_generation"],
            },
            {
                "$set": {
                    "active_turn_id": None,
                    "claim_expires_at": None,
                    "updated_at": now,
                },
            },
            session=session,
        )

    async def _load_recent(self, conversation_id: str, conversation: Document) -> list[Document]:
        cursor = (
            self._database["conversation_turns"]
            .find(
                {
                    "conversation_id": conversation_id,
                    "status": AgentTurnStatus.COMPLETED.value,
                    "sequence": {"$gt": int(conversation["summary_through_sequence"])},
                }
            )
            .sort("sequence", -1)
            .limit(MAX_RECENT_TURNS)
        )
        newest_first = await cursor.to_list(length=MAX_RECENT_TURNS)
        selected: list[Document] = []
        used_chars = 0
        for turn in newest_first:
            request = AgentTurnRequest.model_validate(turn["request"])
            result = AgentTurnResult.model_validate(turn["result"])
            turn_chars = len(_bounded_text(request.objective)) + len(_bounded_text(result.text))
            if selected and used_chars + turn_chars > MAX_RECENT_CONTEXT_CHARS:
                break
            selected.append(turn)
            used_chars += turn_chars
        selected.reverse()
        return selected

    async def _compact(self, conversation_id: str) -> None:
        conversations = self._database["conversations"]
        turns = self._database["conversation_turns"]
        while True:
            conversation = await conversations.find_one({"_id": conversation_id})
            if conversation is None:
                return
            cursor = (
                turns.find(
                    {
                        "conversation_id": conversation_id,
                        "status": AgentTurnStatus.COMPLETED.value,
                        "sequence": {"$gt": int(conversation["summary_through_sequence"])},
                    }
                )
                .sort("sequence", ASCENDING)
                .limit(MAX_RECENT_TURNS + 1)
            )
            candidates = await cursor.to_list(length=MAX_RECENT_TURNS + 1)
            total_chars = sum(
                len(_bounded_text(AgentTurnRequest.model_validate(turn["request"]).objective))
                + len(_bounded_text(AgentTurnResult.model_validate(turn["result"]).text))
                for turn in candidates
            )
            if len(candidates) <= MAX_RECENT_TURNS and total_chars <= MAX_RECENT_CONTEXT_CHARS:
                return
            compacted = candidates[0]
            replacement = extend_summary(str(conversation["summary"]), [compacted])
            write = await conversations.update_one(
                {
                    "_id": conversation_id,
                    "summary_through_sequence": conversation["summary_through_sequence"],
                },
                {
                    "$set": {
                        "summary": replacement,
                        "summary_through_sequence": compacted["sequence"],
                    }
                },
            )
            if write.matched_count != 1:
                continue

    async def read_turn(self, conversation_id: str, run_id: str) -> Document | None:
        return await self._database["conversation_turns"].find_one(
            {"conversation_id": conversation_id, "run_id": run_id}
        )

    async def read_conversation(self, conversation_id: str) -> Document | None:
        return await self._database["conversations"].find_one({"_id": conversation_id})


__all__ = ["RUNTIME_COLLECTION_VALIDATORS", "MongoConversationRepository"]

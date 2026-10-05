import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol, TypeVar, cast

from pymongo.asynchronous.database import AsyncDatabase
from team_agent_contracts import (
    MAX_CITATION_HEADING_LENGTH,
    MAX_CITATION_PATH_LENGTH,
    MAX_CITATION_REPOSITORY_LENGTH,
    MAX_REVISION_LENGTH,
    Authority,
    Citation,
    KnowledgeResult,
    KnowledgeSearchRequest,
    MemoryAuditEvent,
    MemoryAuditListRequest,
    MemoryDecisionRequest,
    MemoryDecisionResponse,
    MemoryMutationResponse,
    MemoryProposal,
    MemoryProposalCreateRequest,
    MemorySearchItem,
    MemorySearchRequest,
    Principal,
    TeamMemory,
)
from team_context_core import InMemoryGovernedMemory, InMemoryKnowledgeIndex, KnowledgeChunk

Document = dict[str, Any]
MAX_MEMORY_STATE_RECORDS = 10_000


def _citation_schema() -> Document:
    return {
        "bsonType": "object",
        "required": ["repository", "path", "revision"],
        "additionalProperties": False,
        "properties": {
            "repository": {
                "bsonType": "string",
                "minLength": 1,
                "maxLength": MAX_CITATION_REPOSITORY_LENGTH,
            },
            "path": {
                "bsonType": "string",
                "minLength": 1,
                "maxLength": MAX_CITATION_PATH_LENGTH,
            },
            "revision": {
                "bsonType": "string",
                "minLength": 1,
                "maxLength": MAX_REVISION_LENGTH,
            },
            "heading": {
                "bsonType": ["string", "null"],
                "maxLength": MAX_CITATION_HEADING_LENGTH,
            },
        },
    }


def _object_schema(required: list[str], properties: Document) -> Document:
    return {
        "$jsonSchema": {
            "bsonType": "object",
            "required": ["_id", *required],
            "additionalProperties": False,
            "properties": {"_id": {"bsonType": "string"}, **properties},
        }
    }


COLLECTION_VALIDATORS: dict[str, Document] = {
    "documents": _object_schema(
        [
            "schema_version",
            "source_id",
            "project",
            "access_groups",
            "authority",
            "title",
            "path",
            "revision",
            "updated_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "project": {"bsonType": "string"},
            "access_groups": {"bsonType": "array", "items": {"bsonType": "string"}},
            "authority": {"enum": ["approved", "proposed", "superseded"]},
            "title": {"bsonType": "string"},
            "path": {"bsonType": "string"},
            "revision": {"bsonType": "string"},
            "updated_at": {"bsonType": "date"},
        },
    ),
    "document_chunks": _object_schema(
        [
            "schema_version",
            "source_id",
            "project",
            "access_groups",
            "authority",
            "title",
            "body",
            "citation",
            "updated_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "project": {"bsonType": "string"},
            "access_groups": {"bsonType": "array", "items": {"bsonType": "string"}},
            "authority": {"enum": ["approved", "proposed", "superseded"]},
            "title": {"bsonType": "string"},
            "body": {"bsonType": "string"},
            "citation": {
                "bsonType": "object",
                "required": ["repository", "path", "revision"],
                "additionalProperties": False,
                "properties": {
                    "repository": {"bsonType": "string"},
                    "path": {"bsonType": "string"},
                    "revision": {"bsonType": "string"},
                    "heading": {"bsonType": ["string", "null"]},
                },
            },
            "updated_at": {"bsonType": "date"},
        },
    ),
    "source_revisions": _object_schema(
        [
            "schema_version",
            "source_id",
            "revision",
            "status",
            "chunk_count",
            "synchronized_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "revision": {"bsonType": "string"},
            "status": {"enum": ["synchronizing", "ready", "failed"]},
            "chunk_count": {"bsonType": "int", "minimum": 0},
            "synchronized_at": {"bsonType": "date"},
            "error": {"bsonType": "string"},
        },
    ),
    "schema_migrations": _object_schema(
        ["schema_version", "applied_at"],
        {
            "schema_version": {"bsonType": "int"},
            "applied_at": {"bsonType": "date"},
        },
    ),
    "memory_proposals": _object_schema(
        [
            "schema_version",
            "project",
            "access_groups",
            "title",
            "body",
            "provenance",
            "evidence",
            "author_id",
            "status",
            "expires_at",
            "created_at",
            "updated_at",
            "revision",
        ],
        {
            "schema_version": {"enum": ["1"]},
            "project": {"bsonType": "string"},
            "access_groups": {"bsonType": "array", "items": {"bsonType": "string"}},
            "title": {"bsonType": "string"},
            "body": {"bsonType": "string"},
            "provenance": _citation_schema(),
            "evidence": {
                "bsonType": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": _citation_schema(),
            },
            "author_id": {"bsonType": "string"},
            "status": {"enum": ["proposed", "approved", "rejected", "expired"]},
            "expires_at": {"bsonType": "date"},
            "supersedes_memory_id": {"bsonType": ["string", "null"]},
            "memory_id": {"bsonType": ["string", "null"]},
            "decision_reason": {"bsonType": ["string", "null"]},
            "decision_author_id": {"bsonType": ["string", "null"]},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "revision": {"bsonType": "int", "minimum": 1},
        },
    ),
    "memories": _object_schema(
        [
            "schema_version",
            "proposal_id",
            "project",
            "access_groups",
            "title",
            "body",
            "provenance",
            "evidence",
            "author_id",
            "approved_by",
            "authority",
            "expires_at",
            "created_at",
            "revision",
        ],
        {
            "schema_version": {"enum": ["1"]},
            "proposal_id": {"bsonType": "string"},
            "project": {"bsonType": "string"},
            "access_groups": {"bsonType": "array", "items": {"bsonType": "string"}},
            "title": {"bsonType": "string"},
            "body": {"bsonType": "string"},
            "provenance": _citation_schema(),
            "evidence": {
                "bsonType": "array",
                "minItems": 1,
                "maxItems": 20,
                "items": _citation_schema(),
            },
            "author_id": {"bsonType": "string"},
            "approved_by": {"bsonType": "string"},
            "authority": {"enum": ["approved"]},
            "expires_at": {"bsonType": "date"},
            "supersedes_memory_id": {"bsonType": ["string", "null"]},
            "superseded_by_memory_id": {"bsonType": ["string", "null"]},
            "expired_at": {"bsonType": ["date", "null"]},
            "created_at": {"bsonType": "date"},
            "revision": {"bsonType": "int", "minimum": 1},
        },
    ),
    "audit_events": _object_schema(
        [
            "schema_version",
            "action",
            "actor_id",
            "project",
            "proposal_id",
            "proposal_revision",
            "idempotency_key",
            "occurred_at",
        ],
        {
            "schema_version": {"enum": ["1"]},
            "action": {"enum": ["proposed", "approved", "rejected", "expired", "superseded"]},
            "actor_id": {"bsonType": "string"},
            "project": {"bsonType": "string"},
            "proposal_id": {"bsonType": "string"},
            "memory_id": {"bsonType": ["string", "null"]},
            "proposal_revision": {"bsonType": "int", "minimum": 1},
            "idempotency_key": {"bsonType": "string"},
            "reason": {"bsonType": ["string", "null"]},
            "occurred_at": {"bsonType": "date"},
        },
    ),
    "memory_idempotency": _object_schema(
        ["schema_version", "fingerprint", "response_type", "response_json"],
        {
            "schema_version": {"enum": ["1"]},
            "fingerprint": {"bsonType": "string"},
            "response_type": {"enum": ["proposal", "decision", "mutation"]},
            "response_json": {"bsonType": "string"},
        },
    ),
}


class MongoContextRepository:
    """Owns context-database schema migrations and durable content synchronization."""

    def __init__(self, database: AsyncDatabase[Document]) -> None:
        self._database = database

    async def migrate(self) -> None:
        existing = set(await self._database.list_collection_names())
        for name, validator in COLLECTION_VALIDATORS.items():
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

        await self._database["document_chunks"].create_index(
            [("project", 1), ("authority", 1), ("access_groups", 1), ("source_id", 1)],
            name="authorized_scope_source",
        )
        await self._database["document_chunks"].create_index(
            [("source_id", 1), ("citation.revision", 1)],
            name="source_revision",
        )
        await self._database["source_revisions"].create_index(
            [("source_id", 1), ("revision", 1)],
            name="source_revision_unique",
            unique=True,
        )
        await self._database["memory_proposals"].create_index(
            [("project", 1), ("status", 1), ("access_groups", 1)],
            name="memory_proposal_scope_status",
        )
        await self._database["memories"].create_index(
            [
                ("project", 1),
                ("authority", 1),
                ("access_groups", 1),
                ("expires_at", 1),
                ("superseded_by_memory_id", 1),
            ],
            name="authoritative_memory_scope",
        )
        await self._database["audit_events"].create_index(
            [("proposal_id", 1), ("occurred_at", 1), ("_id", 1)],
            name="memory_audit_order",
        )
        await self._database["source_revisions"].update_many(
            {},
            {
                "$set": {"schema_version": 3},
                "$unset": {"skill_count": ""},
            },
        )
        await self._database["schema_migrations"].update_one(
            {"_id": "context-schema-v4"},
            {
                "$setOnInsert": {
                    "schema_version": 4,
                    "applied_at": datetime.now(UTC),
                }
            },
            upsert=True,
        )

    async def synchronize(
        self,
        source_id: str,
        revision: str,
        chunks: Sequence[KnowledgeChunk],
    ) -> None:
        synchronized_at = datetime.now(UTC)
        revision_id = f"{source_id}:{revision}"
        revisions = self._database["source_revisions"]
        await revisions.update_one(
            {"_id": revision_id},
            {
                "$set": {
                    "schema_version": 3,
                    "source_id": source_id,
                    "revision": revision,
                    "status": "synchronizing",
                    "chunk_count": 0,
                    "synchronized_at": synchronized_at,
                },
                "$unset": {"error": "", "skill_count": ""},
            },
            upsert=True,
        )

        chunk_ids: list[str] = []
        try:
            for chunk in chunks:
                if chunk.citation.revision != revision:
                    raise ValueError(
                        f"Chunk {chunk.id} revision {chunk.citation.revision!r} "
                        f"does not match synchronized revision {revision!r}"
                    )
                chunk_ids.append(chunk.id)
                document = {
                    "_id": chunk.id,
                    "schema_version": 1,
                    "source_id": source_id,
                    "project": chunk.project,
                    "access_groups": chunk.access_groups,
                    "authority": chunk.authority.value,
                    "title": chunk.title,
                    "path": chunk.citation.path,
                    "revision": revision,
                    "updated_at": synchronized_at,
                }
                persisted_chunk = {
                    "_id": chunk.id,
                    "schema_version": 1,
                    "source_id": source_id,
                    "project": chunk.project,
                    "access_groups": chunk.access_groups,
                    "authority": chunk.authority.value,
                    "title": chunk.title,
                    "body": chunk.body,
                    "citation": chunk.citation.model_dump(mode="json", exclude_none=True),
                    "updated_at": synchronized_at,
                }
                await self._database["documents"].replace_one(
                    {"_id": chunk.id}, document, upsert=True
                )
                await self._database["document_chunks"].replace_one(
                    {"_id": chunk.id}, persisted_chunk, upsert=True
                )

            stale_filter: Document = {
                "source_id": source_id,
                "_id": {"$nin": chunk_ids},
            }
            await self._database["documents"].delete_many(stale_filter)
            await self._database["document_chunks"].delete_many(stale_filter)

            await revisions.update_one(
                {"_id": revision_id},
                {
                    "$set": {
                        "status": "ready",
                        "chunk_count": len(chunks),
                        "synchronized_at": datetime.now(UTC),
                    },
                    "$unset": {"error": ""},
                },
            )
        except Exception as error:
            await revisions.update_one(
                {"_id": revision_id},
                {
                    "$set": {
                        "status": "failed",
                        "chunk_count": len(chunk_ids),
                        "synchronized_at": datetime.now(UTC),
                        "error": str(error)[:1_000],
                    }
                },
            )
            raise


class _AsyncCursor(Protocol):
    async def to_list(self, *, length: int | None) -> list[Mapping[str, Any]]: ...


class KnowledgeChunkCollection(Protocol):
    def find(self, filter: Mapping[str, Any]) -> _AsyncCursor: ...


class MongoKnowledgeIndex:
    """Authorized knowledge retrieval backed by MongoDB candidate selection."""

    def __init__(self, chunks: KnowledgeChunkCollection) -> None:
        self._chunks = chunks

    async def search(
        self, request: KnowledgeSearchRequest, principal: Principal
    ) -> list[KnowledgeResult]:
        if request.project not in principal.projects or not principal.groups:
            return []

        documents = await self._chunks.find(
            {
                "project": request.project,
                "access_groups": {"$in": principal.groups},
                "authority": "approved",
            }
        ).to_list(length=None)
        candidates = [
            KnowledgeChunk(
                id=str(document["_id"]),
                project=str(document["project"]),
                access_groups=list(document["access_groups"]),
                authority=Authority(str(document["authority"])),
                title=str(document["title"]),
                body=str(document["body"]),
                citation=Citation.model_validate(document["citation"]),
            )
            for document in documents
        ]
        return await InMemoryKnowledgeIndex(candidates).search(request, principal)


def _model_document(model: Any) -> Document:
    document = model.model_dump(mode="python", exclude_none=True)
    document["_id"] = document.pop("id")
    return cast(Document, document)


def _model_input(document: Mapping[str, Any]) -> Document:
    value = dict(document)
    value["id"] = value.pop("_id")
    return value


MemoryOperationResult = TypeVar("MemoryOperationResult")


class MongoGovernedMemory:
    """Transaction-backed durable adapter for governed memory."""

    def __init__(self, database: AsyncDatabase[Document]) -> None:
        self._database = database

    async def verify_transactions(self) -> None:
        async with self._database.client.start_session() as session:

            async def write_probe(active_session: Any) -> None:
                await self._database["schema_migrations"].update_one(
                    {"_id": "context-transaction-capability"},
                    {
                        "$setOnInsert": {
                            "schema_version": 4,
                            "applied_at": datetime.now(UTC),
                        }
                    },
                    upsert=True,
                    session=active_session,
                )

            await session.with_transaction(write_probe)

    @staticmethod
    def _receipt_id(principal_id: str, action: str, idempotency_key: str) -> str:
        return json.dumps((principal_id, action, idempotency_key), separators=(",", ":"))

    async def _load(
        self,
        session: Any = None,
        *,
        proposal_filter: Mapping[str, Any] | None = None,
        memory_filter: Mapping[str, Any] | None = None,
        audit_filter: Mapping[str, Any] | None = None,
        receipt_filter: Mapping[str, Any] | None = None,
    ) -> InMemoryGovernedMemory:
        empty_filter: Mapping[str, Any] = {"_id": {"$in": []}}
        proposal_documents = (
            await self._database["memory_proposals"]
            .find(proposal_filter or empty_filter, session=session)
            .to_list(length=MAX_MEMORY_STATE_RECORDS + 1)
        )
        memory_documents = (
            await self._database["memories"]
            .find(memory_filter or empty_filter, session=session)
            .to_list(length=MAX_MEMORY_STATE_RECORDS + 1)
        )
        audit_documents = (
            await self._database["audit_events"]
            .find(audit_filter or empty_filter, session=session)
            .to_list(length=MAX_MEMORY_STATE_RECORDS + 1)
        )
        receipt_documents = (
            await self._database["memory_idempotency"]
            .find(receipt_filter or empty_filter, session=session)
            .to_list(length=MAX_MEMORY_STATE_RECORDS + 1)
        )
        if any(
            len(documents) > MAX_MEMORY_STATE_RECORDS
            for documents in (
                proposal_documents,
                memory_documents,
                audit_documents,
                receipt_documents,
            )
        ):
            raise RuntimeError("Governed memory query exceeded its safe record bound")
        proposals = [
            MemoryProposal.model_validate(_model_input(document)) for document in proposal_documents
        ]
        memories = [
            TeamMemory.model_validate(_model_input(document)) for document in memory_documents
        ]
        audits = [
            MemoryAuditEvent.model_validate(_model_input(document)) for document in audit_documents
        ]
        receipts: dict[tuple[str, str, str], tuple[str, object]] = {}
        for document in receipt_documents:
            raw_key = json.loads(str(document["_id"]))
            key = (str(raw_key[0]), str(raw_key[1]), str(raw_key[2]))
            response_type = document["response_type"]
            if response_type == "proposal":
                response: object = MemoryProposal.model_validate_json(document["response_json"])
            elif response_type == "decision":
                response = MemoryDecisionResponse.model_validate_json(document["response_json"])
            else:
                response = MemoryMutationResponse.model_validate_json(document["response_json"])
            receipts[key] = (str(document["fingerprint"]), response)
        return InMemoryGovernedMemory(
            proposals=proposals,
            memories=memories,
            audits=audits,
            idempotency=receipts,
        )

    async def _save(self, service: InMemoryGovernedMemory, session: Any) -> None:
        proposals, memories, audits, receipts = service.snapshot()
        for proposal in proposals:
            await self._database["memory_proposals"].replace_one(
                {"_id": proposal.id}, _model_document(proposal), upsert=True, session=session
            )
        for memory in memories:
            await self._database["memories"].replace_one(
                {"_id": memory.id}, _model_document(memory), upsert=True, session=session
            )
        audit_ids = [event.id for event in audits]
        existing_audits: set[str] = set()
        if audit_ids:
            existing_audits = {
                str(document["_id"])
                for document in await self._database["audit_events"]
                .find({"_id": {"$in": audit_ids}}, {"_id": 1}, session=session)
                .to_list(length=len(audit_ids))
            }
        new_audits = [_model_document(event) for event in audits if event.id not in existing_audits]
        if new_audits:
            await self._database["audit_events"].insert_many(new_audits, session=session)
        for key, (fingerprint, response) in receipts.items():
            if isinstance(response, MemoryProposal):
                response_type = "proposal"
            elif isinstance(response, MemoryDecisionResponse):
                response_type = "decision"
            elif isinstance(response, MemoryMutationResponse):
                response_type = "mutation"
            else:
                raise TypeError("Unsupported memory idempotency response")
            await self._database["memory_idempotency"].update_one(
                {"_id": json.dumps(key, separators=(",", ":"))},
                {
                    "$setOnInsert": {
                        "schema_version": "1",
                        "fingerprint": fingerprint,
                        "response_type": response_type,
                        "response_json": response.model_dump_json(exclude_none=True),
                    }
                },
                upsert=True,
                session=session,
            )

    async def _transaction(
        self,
        operation: Callable[[InMemoryGovernedMemory], Awaitable[MemoryOperationResult]],
        *,
        proposal_filter: Mapping[str, Any] | None = None,
        memory_filter: Mapping[str, Any] | None = None,
        receipt_filter: Mapping[str, Any] | None = None,
    ) -> MemoryOperationResult:
        async with self._database.client.start_session() as session:

            async def execute(active_session: Any) -> Any:
                service = await self._load(
                    active_session,
                    proposal_filter=proposal_filter,
                    memory_filter=memory_filter,
                    receipt_filter=receipt_filter,
                )
                result = await operation(service)
                await self._save(service, active_session)
                return result

            return cast(MemoryOperationResult, await session.with_transaction(execute))

    async def propose(
        self,
        request: MemoryProposalCreateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> MemoryProposal:
        async def operation(service: InMemoryGovernedMemory) -> MemoryProposal:
            return await service.propose(request, principal, idempotency_key)

        receipt_id = self._receipt_id(principal.id, "propose", idempotency_key)
        return await self._transaction(operation, receipt_filter={"_id": receipt_id})

    async def decide(
        self,
        proposal_id: str,
        request: MemoryDecisionRequest,
        principal: Principal,
        idempotency_key: str,
        *,
        approve: bool,
    ) -> MemoryDecisionResponse:
        async def operation(service: InMemoryGovernedMemory) -> MemoryDecisionResponse:
            return await service.decide(
                proposal_id, request, principal, idempotency_key, approve=approve
            )

        action = "approve" if approve else "reject"
        receipt_id = self._receipt_id(principal.id, f"{action}:{proposal_id}", idempotency_key)
        proposal_document = await self._database["memory_proposals"].find_one(
            {"_id": proposal_id}, {"supersedes_memory_id": 1}
        )
        memory_filter: Mapping[str, Any] | None = None
        if proposal_document is not None and proposal_document.get("supersedes_memory_id"):
            memory_filter = {"_id": proposal_document["supersedes_memory_id"]}
        return await self._transaction(
            operation,
            proposal_filter={"_id": proposal_id},
            memory_filter=memory_filter,
            receipt_filter={"_id": receipt_id},
        )

    async def expire(
        self,
        memory_id: str,
        request: MemoryDecisionRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> MemoryMutationResponse:
        async def operation(service: InMemoryGovernedMemory) -> MemoryMutationResponse:
            return await service.expire(memory_id, request, principal, idempotency_key)

        receipt_id = self._receipt_id(principal.id, f"expire:{memory_id}", idempotency_key)
        memory_document = await self._database["memories"].find_one(
            {"_id": memory_id}, {"proposal_id": 1}
        )
        proposal_filter: Mapping[str, Any] | None = None
        if memory_document is not None:
            proposal_filter = {"_id": memory_document["proposal_id"]}
        return await self._transaction(
            operation,
            proposal_filter=proposal_filter,
            memory_filter={"_id": memory_id},
            receipt_filter={"_id": receipt_id},
        )

    async def search(
        self, request: MemorySearchRequest, principal: Principal
    ) -> tuple[list[MemorySearchItem], str | None]:
        if request.project not in principal.projects or not principal.groups:
            return [], None
        service = await self._load(
            memory_filter={
                "project": request.project,
                "access_groups": {"$in": principal.groups},
                "authority": "approved",
                "expires_at": {"$gt": datetime.now(UTC)},
                "superseded_by_memory_id": {"$exists": False},
                "expired_at": {"$exists": False},
            }
        )
        return await service.search(request, principal)

    async def audit(
        self, request: MemoryAuditListRequest, principal: Principal
    ) -> tuple[list[MemoryAuditEvent], str | None]:
        if request.project not in principal.projects or not principal.groups:
            from team_context_core import MemoryNotFoundError

            raise MemoryNotFoundError("Memory proposal not found")
        service = await self._load(
            proposal_filter={
                "_id": request.proposal_id,
                "project": request.project,
                "access_groups": {"$in": principal.groups},
            },
            audit_filter={"proposal_id": request.proposal_id, "project": request.project},
        )
        return await service.audit(request, principal)


__all__ = [
    "COLLECTION_VALIDATORS",
    "KnowledgeChunkCollection",
    "MongoContextRepository",
    "MongoGovernedMemory",
    "MongoKnowledgeIndex",
]

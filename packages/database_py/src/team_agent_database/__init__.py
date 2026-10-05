import json
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Any, Protocol, TypeVar, cast

from pymongo.asynchronous.database import AsyncDatabase
from pymongo.errors import OperationFailure
from team_agent_contracts import (
    MAX_CITATION_HEADING_LENGTH,
    MAX_CITATION_PATH_LENGTH,
    MAX_CITATION_REPOSITORY_LENGTH,
    MAX_REVISION_LENGTH,
    Citation,
    KnowledgeResult,
    KnowledgeSearchRequest,
    MemoryAuditEvent,
    MemoryAuditListRequest,
    MemoryExpireRequest,
    MemoryMutationResponse,
    MemorySearchItem,
    MemorySearchRequest,
    Principal,
    SharedMemoryCreateRequest,
    SharedMemoryResponse,
    SharedMemoryUpdateRequest,
    TeamMemory,
)
from team_context_core import InMemoryKnowledgeIndex, InMemorySharedMemory, KnowledgeChunk

Document = dict[str, Any]
MAX_MEMORY_STATE_RECORDS = 10_000
MAX_KNOWLEDGE_CANDIDATES = 2_000


class ProjectionActivationError(RuntimeError):
    pass


class ProjectionActivationUncertainError(ProjectionActivationError):
    pass


def knowledge_projection_hash(chunks: Sequence[KnowledgeChunk]) -> str:
    """Return a deterministic identity for the complete projected content."""
    serialized = [
        chunk.model_dump(mode="json", exclude_none=True)
        for chunk in sorted(chunks, key=lambda item: item.id)
    ]
    payload = json.dumps(serialized, sort_keys=True, separators=(",", ":"))
    return sha256(payload.encode()).hexdigest()


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
    "knowledge_documents": _object_schema(
        [
            "schema_version",
            "source_id",
            "logical_id",
            "projection_hash",
            "project",
            "title",
            "path",
            "revision",
            "updated_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "logical_id": {"bsonType": "string"},
            "projection_hash": {"bsonType": "string"},
            "project": {"bsonType": "string"},
            "title": {"bsonType": "string"},
            "path": {"bsonType": "string"},
            "revision": {"bsonType": "string"},
            "updated_at": {"bsonType": "date"},
        },
    ),
    "knowledge_chunks": _object_schema(
        [
            "schema_version",
            "source_id",
            "logical_id",
            "projection_hash",
            "revision",
            "project",
            "title",
            "body",
            "citation",
            "updated_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "logical_id": {"bsonType": "string"},
            "projection_hash": {"bsonType": "string"},
            "revision": {"bsonType": "string"},
            "project": {"bsonType": "string"},
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
    "knowledge_revisions": _object_schema(
        [
            "schema_version",
            "source_id",
            "revision",
            "status",
            "chunk_count",
            "projection_hash",
            "synchronized_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "revision": {"bsonType": "string"},
            "status": {"enum": ["synchronizing", "ready", "failed", "activation_uncertain"]},
            "chunk_count": {"bsonType": "int", "minimum": 0},
            "projection_hash": {"bsonType": "string"},
            "synchronized_at": {"bsonType": "date"},
            "error": {"bsonType": "string"},
        },
    ),
    "active_knowledge_revisions": _object_schema(
        [
            "schema_version",
            "source_id",
            "revision",
            "projection_hash",
            "chunk_count",
            "activated_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "revision": {"bsonType": "string"},
            "projection_hash": {"bsonType": "string"},
            "chunk_count": {"bsonType": "int", "minimum": 0},
            "activated_at": {"bsonType": "date"},
        },
    ),
    "schema_migrations": _object_schema(
        ["schema_version", "applied_at"],
        {
            "schema_version": {"bsonType": "int"},
            "applied_at": {"bsonType": "date"},
        },
    ),
    "shared_memories": _object_schema(
        [
            "schema_version",
            "project",
            "title",
            "body",
            "provenance",
            "evidence",
            "author_id",
            "last_modified_by",
            "canonicality",
            "expires_at",
            "created_at",
            "updated_at",
            "revision",
        ],
        {
            "schema_version": {"enum": ["1"]},
            "project": {"bsonType": "string"},
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
            "last_modified_by": {"bsonType": "string"},
            "canonicality": {"enum": ["supplemental"]},
            "expires_at": {"bsonType": "date"},
            "supersedes_memory_id": {"bsonType": ["string", "null"]},
            "superseded_by_memory_id": {"bsonType": ["string", "null"]},
            "expired_at": {"bsonType": ["date", "null"]},
            "created_at": {"bsonType": "date"},
            "updated_at": {"bsonType": "date"},
            "revision": {"bsonType": "int", "minimum": 1},
        },
    ),
    "shared_memory_audit_events": _object_schema(
        [
            "schema_version",
            "action",
            "actor_id",
            "project",
            "memory_id",
            "memory_revision",
            "idempotency_key",
            "occurred_at",
        ],
        {
            "schema_version": {"enum": ["1"]},
            "action": {"enum": ["created", "updated", "expired", "superseded"]},
            "actor_id": {"bsonType": "string"},
            "project": {"bsonType": "string"},
            "memory_id": {"bsonType": "string"},
            "memory_revision": {"bsonType": "int", "minimum": 1},
            "idempotency_key": {"bsonType": "string"},
            "reason": {"bsonType": ["string", "null"]},
            "occurred_at": {"bsonType": "date"},
        },
    ),
    "shared_memory_idempotency": _object_schema(
        ["schema_version", "fingerprint", "response_type", "response_json"],
        {
            "schema_version": {"enum": ["1"]},
            "fingerprint": {"bsonType": "string"},
            "response_type": {"enum": ["memory", "mutation"]},
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
        legacy_memory_collections = {
            "memory_proposals",
            "memories",
            "audit_events",
            "memory_idempotency",
        }
        for name in sorted(existing.intersection(legacy_memory_collections)):
            if await self._database[name].find_one({}, {"_id": 1}) is not None:
                raise RuntimeError(
                    "Legacy governed-memory data is present; export or archive it before "
                    "enabling schema v5. It cannot be reinterpreted as supplemental memory."
                )
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

        await self._database["knowledge_chunks"].create_index(
            [("project", 1), ("source_id", 1), ("revision", 1), ("projection_hash", 1)],
            name="project_source_revision_projection",
        )
        await self._database["knowledge_chunks"].create_index(
            [("source_id", 1), ("citation.revision", 1)],
            name="source_revision",
        )
        try:
            await self._database["knowledge_revisions"].drop_index("source_revision_unique")
        except OperationFailure as error:
            if error.code != 27:  # IndexNotFound
                raise
        await self._database["knowledge_revisions"].create_index(
            [("source_id", 1), ("revision", 1), ("projection_hash", 1)],
            name="source_revision_projection_unique",
            unique=True,
        )
        await self._database["shared_memories"].create_index(
            [
                ("project", 1),
                ("canonicality", 1),
                ("expires_at", 1),
                ("superseded_by_memory_id", 1),
            ],
            name="supplemental_memory_project",
        )
        await self._database["shared_memory_audit_events"].create_index(
            [("memory_id", 1), ("occurred_at", 1), ("_id", 1)],
            name="memory_audit_order",
        )
        await self._database["schema_migrations"].update_one(
            {"_id": "context-schema-v5"},
            {
                "$setOnInsert": {
                    "schema_version": 5,
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
        *,
        expected_active_revision: str | None = None,
        allow_same_revision_republish: bool = False,
    ) -> None:
        synchronized_at = datetime.now(UTC)
        projection_hash = knowledge_projection_hash(chunks)
        revision_id = f"{source_id}:{revision}:{projection_hash}"
        revisions = self._database["knowledge_revisions"]
        active_revisions = self._database["active_knowledge_revisions"]
        previously_active = await active_revisions.find_one({"_id": source_id})
        active_is_candidate = (
            previously_active is not None
            and previously_active.get("revision") == revision
            and previously_active.get("projection_hash") == projection_hash
        )
        if previously_active is not None and active_is_candidate:
            expected_count = previously_active.get("chunk_count")
            staged = await self._database["knowledge_chunks"].count_documents(
                {
                    "source_id": source_id,
                    "revision": revision,
                    "projection_hash": projection_hash,
                },
                limit=len(chunks) + 1,
            )
            candidate = await revisions.find_one({"_id": revision_id})
            if (
                candidate is not None
                and candidate.get("status") in {"ready", "activation_uncertain"}
                and expected_count == len(chunks)
                and staged == len(chunks)
            ):
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
                return
        observed_revision = (
            str(previously_active["revision"]) if previously_active is not None else None
        )
        same_mutable_revision = (
            previously_active is not None
            and observed_revision == revision
            and allow_same_revision_republish
        )
        if not same_mutable_revision and observed_revision != expected_active_revision:
            raise ProjectionActivationError(
                "The active knowledge revision does not match the expected predecessor"
            )
        if (
            previously_active is not None
            and observed_revision == revision
            and not active_is_candidate
            and not allow_same_revision_republish
        ):
            raise ProjectionActivationError("Immutable knowledge revision content changed")
        await revisions.update_one(
            {"_id": revision_id},
            {
                "$set": {
                    "schema_version": 3,
                    "source_id": source_id,
                    "revision": revision,
                    "projection_hash": projection_hash,
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
                projection_id = sha256(
                    f"{source_id}\0{revision}\0{projection_hash}\0{chunk.id}".encode()
                ).hexdigest()
                chunk_ids.append(projection_id)
                document = {
                    "_id": projection_id,
                    "schema_version": 1,
                    "source_id": source_id,
                    "logical_id": chunk.id,
                    "projection_hash": projection_hash,
                    "project": chunk.project,
                    "title": chunk.title,
                    "path": chunk.citation.path,
                    "revision": revision,
                    "updated_at": synchronized_at,
                }
                persisted_chunk = {
                    "_id": projection_id,
                    "schema_version": 1,
                    "source_id": source_id,
                    "logical_id": chunk.id,
                    "projection_hash": projection_hash,
                    "revision": revision,
                    "project": chunk.project,
                    "title": chunk.title,
                    "body": chunk.body,
                    "citation": chunk.citation.model_dump(mode="json", exclude_none=True),
                    "updated_at": synchronized_at,
                }
                await self._database["knowledge_documents"].replace_one(
                    {"_id": projection_id}, document, upsert=True
                )
                await self._database["knowledge_chunks"].replace_one(
                    {"_id": projection_id}, persisted_chunk, upsert=True
                )

            revision_filter: Document = {
                "source_id": source_id,
                "revision": revision,
                "projection_hash": projection_hash,
            }
            staged = await self._database["knowledge_chunks"].count_documents(
                revision_filter, limit=len(chunks) + 1
            )
            if staged != len(chunks):
                raise RuntimeError("Staged knowledge revision is incomplete")

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
            active_document = {
                "_id": source_id,
                "schema_version": 1,
                "source_id": source_id,
                "revision": revision,
                "projection_hash": projection_hash,
                "chunk_count": len(chunks),
                "activated_at": datetime.now(UTC),
            }
            if previously_active is None:
                try:
                    await active_revisions.insert_one(active_document)
                except Exception as error:
                    try:
                        winner = await active_revisions.find_one({"_id": source_id})
                    except Exception as read_error:
                        raise ProjectionActivationUncertainError(
                            "Knowledge activation acknowledgement could not be reconciled"
                        ) from read_error
                    if (
                        winner is None
                        or winner.get("revision") != revision
                        or winner.get("projection_hash") != projection_hash
                    ):
                        raise ProjectionActivationError(
                            "A concurrent synchronization activated another revision"
                        ) from error
            elif not active_is_candidate:
                activation_filter: Document = {
                    "_id": source_id,
                    "revision": observed_revision,
                    "projection_hash": previously_active.get("projection_hash"),
                }
                try:
                    activation = await active_revisions.replace_one(
                        activation_filter,
                        active_document,
                        upsert=False,
                    )
                except Exception as error:
                    try:
                        winner = await active_revisions.find_one({"_id": source_id})
                    except Exception as read_error:
                        raise ProjectionActivationUncertainError(
                            "Knowledge activation acknowledgement could not be reconciled"
                        ) from read_error
                    if (
                        winner is None
                        or winner.get("revision") != revision
                        or winner.get("projection_hash") != projection_hash
                    ):
                        raise ProjectionActivationError(
                            "A concurrent synchronization activated another revision"
                        ) from error
                    activation = None
                if activation is not None and activation.matched_count != 1:
                    winner = await active_revisions.find_one({"_id": source_id})
                    if (
                        winner is None
                        or winner.get("revision") != revision
                        or winner.get("projection_hash") != projection_hash
                    ):
                        raise ProjectionActivationError(
                            "A concurrent synchronization activated another revision"
                        )
        except ProjectionActivationUncertainError as error:
            await revisions.update_one(
                {"_id": revision_id},
                {
                    "$set": {
                        "status": "activation_uncertain",
                        "chunk_count": len(chunk_ids),
                        "synchronized_at": datetime.now(UTC),
                        "error": str(error)[:1_000],
                    }
                },
            )
            raise
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

    def __init__(
        self,
        chunks: KnowledgeChunkCollection,
        active_revisions: KnowledgeChunkCollection,
        source_id: str,
    ) -> None:
        self._chunks = chunks
        self._active_revisions = active_revisions
        self._source_id = source_id

    async def search(
        self, request: KnowledgeSearchRequest, principal: Principal
    ) -> list[KnowledgeResult]:
        if request.project not in principal.projects:
            return []

        active = await self._active_revisions.find({"_id": self._source_id}).to_list(length=2)
        if len(active) > 1:
            raise RuntimeError("Active knowledge source pointer is not unique")
        if not active:
            return []
        documents = await self._chunks.find(
            {
                "project": request.project,
                "source_id": self._source_id,
                "revision": active[0]["revision"],
                "projection_hash": active[0]["projection_hash"],
            }
        ).to_list(length=MAX_KNOWLEDGE_CANDIDATES + 1)
        if len(documents) > MAX_KNOWLEDGE_CANDIDATES:
            raise RuntimeError("Knowledge candidate count exceeded its safe bound")
        candidates = [
            KnowledgeChunk(
                id=str(document["logical_id"]),
                project=str(document["project"]),
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


class MongoSharedMemory:
    """Transaction-backed durable adapter for project-scoped supplemental memory."""

    def __init__(self, database: AsyncDatabase[Document]) -> None:
        self._database = database

    async def verify_transactions(self) -> None:
        async with self._database.client.start_session() as session:

            async def write_probe(active_session: Any) -> None:
                await self._database["schema_migrations"].update_one(
                    {"_id": "context-transaction-capability"},
                    {
                        "$setOnInsert": {
                            "schema_version": 5,
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
        memory_filter: Mapping[str, Any] | None = None,
        audit_filter: Mapping[str, Any] | None = None,
        receipt_filter: Mapping[str, Any] | None = None,
    ) -> InMemorySharedMemory:
        empty_filter: Mapping[str, Any] = {"_id": {"$in": []}}
        memory_documents = (
            await self._database["shared_memories"]
            .find(memory_filter or empty_filter, session=session)
            .to_list(length=MAX_MEMORY_STATE_RECORDS + 1)
        )
        audit_documents = (
            await self._database["shared_memory_audit_events"]
            .find(audit_filter or empty_filter, session=session)
            .to_list(length=MAX_MEMORY_STATE_RECORDS + 1)
        )
        receipt_documents = (
            await self._database["shared_memory_idempotency"]
            .find(receipt_filter or empty_filter, session=session)
            .to_list(length=MAX_MEMORY_STATE_RECORDS + 1)
        )
        if any(
            len(documents) > MAX_MEMORY_STATE_RECORDS
            for documents in (
                memory_documents,
                audit_documents,
                receipt_documents,
            )
        ):
            raise RuntimeError("Shared memory query exceeded its safe record bound")
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
            if response_type == "memory":
                response: object = SharedMemoryResponse.model_validate_json(
                    document["response_json"]
                )
            else:
                response = MemoryMutationResponse.model_validate_json(document["response_json"])
            receipts[key] = (str(document["fingerprint"]), response)
        return InMemorySharedMemory(
            memories=memories,
            audits=audits,
            idempotency=receipts,
        )

    async def _save(self, service: InMemorySharedMemory, session: Any) -> None:
        memories, audits, receipts = service.snapshot()
        for memory in memories:
            await self._database["shared_memories"].replace_one(
                {"_id": memory.id}, _model_document(memory), upsert=True, session=session
            )
        audit_ids = [event.id for event in audits]
        existing_audits: set[str] = set()
        if audit_ids:
            existing_audits = {
                str(document["_id"])
                for document in await self._database["shared_memory_audit_events"]
                .find({"_id": {"$in": audit_ids}}, {"_id": 1}, session=session)
                .to_list(length=len(audit_ids))
            }
        new_audits = [_model_document(event) for event in audits if event.id not in existing_audits]
        if new_audits:
            await self._database["shared_memory_audit_events"].insert_many(
                new_audits, session=session
            )
        for key, (fingerprint, response) in receipts.items():
            if isinstance(response, SharedMemoryResponse):
                response_type = "memory"
            elif isinstance(response, MemoryMutationResponse):
                response_type = "mutation"
            else:
                raise TypeError("Unsupported memory idempotency response")
            await self._database["shared_memory_idempotency"].update_one(
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
        operation: Callable[[InMemorySharedMemory], Awaitable[MemoryOperationResult]],
        *,
        memory_filter: Mapping[str, Any] | None = None,
        receipt_filter: Mapping[str, Any] | None = None,
    ) -> MemoryOperationResult:
        async with self._database.client.start_session() as session:

            async def execute(active_session: Any) -> Any:
                service = await self._load(
                    active_session,
                    memory_filter=memory_filter,
                    receipt_filter=receipt_filter,
                )
                result = await operation(service)
                await self._save(service, active_session)
                return result

            return cast(MemoryOperationResult, await session.with_transaction(execute))

    async def create(
        self,
        request: SharedMemoryCreateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> SharedMemoryResponse:
        async def operation(service: InMemorySharedMemory) -> SharedMemoryResponse:
            return await service.create(request, principal, idempotency_key)

        receipt_id = self._receipt_id(principal.id, "create", idempotency_key)
        memory_filter: Mapping[str, Any] | None = None
        if request.supersedes_memory_id is not None:
            memory_filter = {"_id": request.supersedes_memory_id}
        return await self._transaction(
            operation,
            memory_filter=memory_filter,
            receipt_filter={"_id": receipt_id},
        )

    async def update(
        self,
        memory_id: str,
        request: SharedMemoryUpdateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> SharedMemoryResponse:
        async def operation(service: InMemorySharedMemory) -> SharedMemoryResponse:
            return await service.update(memory_id, request, principal, idempotency_key)

        receipt_id = self._receipt_id(principal.id, f"update:{memory_id}", idempotency_key)
        return await self._transaction(
            operation,
            memory_filter={"_id": memory_id},
            receipt_filter={"_id": receipt_id},
        )

    async def expire(
        self,
        memory_id: str,
        request: MemoryExpireRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> MemoryMutationResponse:
        async def operation(service: InMemorySharedMemory) -> MemoryMutationResponse:
            return await service.expire(memory_id, request, principal, idempotency_key)

        receipt_id = self._receipt_id(principal.id, f"expire:{memory_id}", idempotency_key)
        return await self._transaction(
            operation,
            memory_filter={"_id": memory_id},
            receipt_filter={"_id": receipt_id},
        )

    async def search(
        self, request: MemorySearchRequest, principal: Principal
    ) -> tuple[list[MemorySearchItem], str | None]:
        if request.project not in principal.projects:
            return [], None
        service = await self._load(
            memory_filter={
                "project": request.project,
                "canonicality": "supplemental",
                "expires_at": {"$gt": datetime.now(UTC)},
                "superseded_by_memory_id": {"$exists": False},
                "expired_at": {"$exists": False},
            }
        )
        return await service.search(request, principal)

    async def audit(
        self, request: MemoryAuditListRequest, principal: Principal
    ) -> tuple[list[MemoryAuditEvent], str | None]:
        if request.project not in principal.projects:
            from team_context_core import MemoryNotFoundError

            raise MemoryNotFoundError("Memory not found")
        service = await self._load(
            memory_filter={
                "_id": request.memory_id,
                "project": request.project,
            },
            audit_filter={"memory_id": request.memory_id, "project": request.project},
        )
        return await service.audit(request, principal)


__all__ = [
    "COLLECTION_VALIDATORS",
    "MAX_KNOWLEDGE_CANDIDATES",
    "KnowledgeChunkCollection",
    "MongoContextRepository",
    "MongoKnowledgeIndex",
    "MongoSharedMemory",
    "ProjectionActivationError",
    "ProjectionActivationUncertainError",
    "knowledge_projection_hash",
]

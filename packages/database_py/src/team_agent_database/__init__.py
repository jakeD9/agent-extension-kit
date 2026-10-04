from collections.abc import Mapping, Sequence
from datetime import UTC, datetime
from typing import Any, Protocol

from pymongo.asynchronous.database import AsyncDatabase
from team_agent_contracts import (
    Authority,
    Citation,
    KnowledgeResult,
    KnowledgeSearchRequest,
    Principal,
    SkillDetail,
    SkillListRequest,
    SkillSummary,
)
from team_context_core import (
    InMemoryKnowledgeIndex,
    KnowledgeChunk,
    SkillPackage,
    decode_skill_cursor,
    encode_skill_cursor,
)

Document = dict[str, Any]


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
            "skill_count",
            "synchronized_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "revision": {"bsonType": "string"},
            "status": {"enum": ["synchronizing", "ready", "failed"]},
            "chunk_count": {"bsonType": "int", "minimum": 0},
            "skill_count": {"bsonType": "int", "minimum": 0},
            "synchronized_at": {"bsonType": "date"},
            "error": {"bsonType": "string"},
        },
    ),
    "skills": _object_schema(
        [
            "schema_version",
            "source_id",
            "name",
            "description",
            "version",
            "projects",
            "access_groups",
            "allowed_tools",
            "body",
            "citation",
            "updated_at",
        ],
        {
            "schema_version": {"bsonType": "int"},
            "source_id": {"bsonType": "string"},
            "name": {"bsonType": "string"},
            "description": {"bsonType": "string"},
            "version": {"bsonType": "string"},
            "projects": {"bsonType": "array", "items": {"bsonType": "string"}},
            "access_groups": {"bsonType": "array", "items": {"bsonType": "string"}},
            "allowed_tools": {"bsonType": "array", "items": {"bsonType": "string"}},
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
    "schema_migrations": _object_schema(
        ["schema_version", "applied_at"],
        {
            "schema_version": {"bsonType": "int"},
            "applied_at": {"bsonType": "date"},
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
        await self._database["skills"].create_index(
            [("projects", 1), ("name", 1), ("_id", 1)],
            name="authorized_project_name",
        )
        await self._database["skills"].create_index(
            [("access_groups", 1), ("name", 1), ("_id", 1)],
            name="authorized_group_name",
        )
        await self._database["skills"].create_index(
            [("source_id", 1), ("name", 1)],
            name="source_skill_name_unique",
            unique=True,
        )
        await self._database["skills"].create_index(
            [("source_id", 1), ("citation.revision", 1)],
            name="skill_source_revision",
        )
        await self._database["schema_migrations"].update_one(
            {"_id": "context-schema-v2"},
            {
                "$setOnInsert": {
                    "schema_version": 2,
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
        skills: Sequence[SkillPackage],
    ) -> None:
        synchronized_at = datetime.now(UTC)
        revision_id = f"{source_id}:{revision}"
        revisions = self._database["source_revisions"]
        await revisions.update_one(
            {"_id": revision_id},
            {
                "$set": {
                    "schema_version": 2,
                    "source_id": source_id,
                    "revision": revision,
                    "status": "synchronizing",
                    "chunk_count": 0,
                    "skill_count": 0,
                    "synchronized_at": synchronized_at,
                },
                "$unset": {"error": ""},
            },
            upsert=True,
        )

        chunk_ids: list[str] = []
        skill_ids: list[str] = []
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

            for skill in skills:
                if skill.citation.revision != revision:
                    raise ValueError(
                        f"Skill {skill.id} revision {skill.citation.revision!r} "
                        f"does not match synchronized revision {revision!r}"
                    )
                skill_ids.append(skill.id)
                persisted_skill = {
                    "_id": skill.id,
                    "schema_version": 1,
                    "source_id": source_id,
                    "name": skill.name,
                    "description": skill.description,
                    "version": skill.version,
                    "projects": skill.projects,
                    "access_groups": skill.access_groups,
                    "allowed_tools": skill.allowed_tools,
                    "body": skill.body,
                    "citation": skill.citation.model_dump(mode="json", exclude_none=True),
                    "updated_at": synchronized_at,
                }
                await self._database["skills"].replace_one(
                    {"_id": skill.id}, persisted_skill, upsert=True
                )
            await self._database["skills"].delete_many(
                {"source_id": source_id, "_id": {"$nin": skill_ids}}
            )
            await revisions.update_one(
                {"_id": revision_id},
                {
                    "$set": {
                        "status": "ready",
                        "chunk_count": len(chunks),
                        "skill_count": len(skills),
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
                        "skill_count": len(skill_ids),
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


class _SkillCursor(_AsyncCursor, Protocol):
    def sort(self, key: str, direction: int) -> "_SkillCursor": ...

    def limit(self, value: int) -> "_SkillCursor": ...


class SkillCollection(Protocol):
    def find(self, filter: Mapping[str, Any]) -> _SkillCursor: ...

    async def find_one(self, filter: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


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


def _skill_summary(document: Mapping[str, Any]) -> SkillSummary:
    return SkillSummary(
        name=str(document["name"]),
        description=str(document["description"]),
        version=str(document["version"]),
        projects=list(document["projects"]),
        access_groups=list(document["access_groups"]),
        allowed_tools=list(document["allowed_tools"]),
        citation=Citation.model_validate(document["citation"]),
    )


class MongoSkillCatalog:
    """Authorized, revision-pinned skill discovery backed by MongoDB."""

    def __init__(self, skills: SkillCollection, *, source_id: str) -> None:
        if not source_id:
            raise ValueError("A skill catalog source ID is required")
        self._skills = skills
        self._source_id = source_id

    async def list(
        self, request: SkillListRequest, principal: Principal
    ) -> tuple[list[SkillSummary], str | None]:
        if request.project not in principal.projects or not principal.groups:
            return [], None
        query: Document = {
            "source_id": self._source_id,
            "projects": request.project,
            "access_groups": {"$in": principal.groups},
        }
        if request.cursor:
            query["_id"] = {"$gt": decode_skill_cursor(request.cursor, request.project)}
        documents = await (
            self._skills.find(query).sort("_id", 1).limit(request.limit + 1).to_list(length=None)
        )
        visible = documents[: request.limit]
        next_cursor = None
        if len(documents) > request.limit:
            next_cursor = encode_skill_cursor(request.project, str(visible[-1]["_id"]))
        return [_skill_summary(document) for document in visible], next_cursor

    async def get(self, name: str, project: str, principal: Principal) -> SkillDetail | None:
        if project not in principal.projects or not principal.groups:
            return None
        document = await self._skills.find_one(
            {
                "source_id": self._source_id,
                "name": name,
                "projects": project,
                "access_groups": {"$in": principal.groups},
            }
        )
        if document is None:
            return None
        summary = _skill_summary(document)
        return SkillDetail(**summary.model_dump(), body=str(document["body"]))


__all__ = [
    "COLLECTION_VALIDATORS",
    "KnowledgeChunkCollection",
    "MongoContextRepository",
    "MongoKnowledgeIndex",
    "MongoSkillCatalog",
    "SkillCollection",
]

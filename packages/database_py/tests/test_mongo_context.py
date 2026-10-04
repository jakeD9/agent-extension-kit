import asyncio
from typing import Any

from team_agent_contracts import Authority, Citation, KnowledgeSearchRequest, Principal
from team_agent_database import MongoContextRepository, MongoKnowledgeIndex
from team_context_core import KnowledgeChunk


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    async def to_list(self, *, length: int | None) -> list[dict[str, Any]]:
        assert length is None
        return self._documents


class _Chunks:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self.query: dict[str, Any] | None = None

    def find(self, query: dict[str, Any]) -> _Cursor:
        self.query = query
        return _Cursor(self._documents)


class _MigrationCollection:
    def __init__(self) -> None:
        self.indexes: list[tuple[list[tuple[str, int]], str, bool]] = []
        self.updates: list[tuple[dict[str, Any], dict[str, Any], bool]] = []
        self.documents: dict[str, dict[str, Any]] = {}

    async def create_index(
        self, keys: list[tuple[str, int]], *, name: str, unique: bool = False
    ) -> str:
        self.indexes.append((keys, name, unique))
        return name

    async def update_one(
        self, query: dict[str, Any], update: dict[str, Any], *, upsert: bool = False
    ) -> None:
        self.updates.append((query, update, upsert))
        document = self.documents.setdefault(query["_id"], {"_id": query["_id"]})
        document.update(update.get("$setOnInsert", {}))
        document.update(update.get("$set", {}))

    async def replace_one(
        self, query: dict[str, Any], replacement: dict[str, Any], *, upsert: bool = False
    ) -> None:
        assert upsert is True
        self.documents[query["_id"]] = replacement

    async def delete_many(self, query: dict[str, Any]) -> None:
        retained = set(query["_id"]["$nin"])
        self.documents = {
            key: value
            for key, value in self.documents.items()
            if value.get("source_id") != query["source_id"] or key in retained
        }


class _MigrationDatabase:
    def __init__(self) -> None:
        self.collections: dict[str, _MigrationCollection] = {}
        self.validators: dict[str, dict[str, Any]] = {}

    async def list_collection_names(self) -> list[str]:
        return list(self.collections)

    async def create_collection(
        self,
        name: str,
        *,
        validator: dict[str, Any],
        validationLevel: str,
        validationAction: str,
    ) -> None:
        assert validationLevel == "strict"
        assert validationAction == "error"
        self.collections[name] = _MigrationCollection()
        self.validators[name] = validator

    def __getitem__(self, name: str) -> _MigrationCollection:
        return self.collections[name]


def test_search_filters_storage_candidates_before_scoring() -> None:
    chunks = _Chunks(
        [
            {
                "_id": "approved",
                "project": "event-ingestion",
                "access_groups": ["engineering"],
                "authority": "approved",
                "title": "Stable idempotency keys",
                "body": "Use the vendor event identifier as the idempotency key.",
                "citation": {
                    "repository": "sample",
                    "path": "knowledge/architecture/idempotency.md",
                    "revision": "abc123",
                    "heading": "Stable event identity",
                },
            }
        ]
    )
    index = MongoKnowledgeIndex(chunks)

    results = asyncio.run(
        index.search(
            KnowledgeSearchRequest(query="vendor idempotency", project="event-ingestion"),
            Principal(id="dev", groups=["engineering"], projects=["event-ingestion"]),
        )
    )

    assert chunks.query == {
        "project": "event-ingestion",
        "access_groups": {"$in": ["engineering"]},
        "authority": "approved",
    }
    assert [result.id for result in results] == ["approved"]
    assert results[0].citation.revision == "abc123"


def test_migration_creates_validated_context_collections_and_indexes() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)

    asyncio.run(repository.migrate())

    assert set(database.collections) == {
        "document_chunks",
        "documents",
        "schema_migrations",
        "source_revisions",
    }
    assert set(database.validators) == set(database.collections)
    assert all(
        validator["$jsonSchema"]["additionalProperties"] is False
        for validator in database.validators.values()
    )
    assert database.collections["document_chunks"].indexes == [
        (
            [("project", 1), ("authority", 1), ("access_groups", 1), ("source_id", 1)],
            "authorized_scope_source",
            False,
        ),
        ([("source_id", 1), ("citation.revision", 1)], "source_revision", False),
    ]
    assert database.collections["source_revisions"].indexes == [
        ([("source_id", 1), ("revision", 1)], "source_revision_unique", True)
    ]


def test_synchronize_replaces_source_content_and_records_ready_revision() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    old = KnowledgeChunk(
        id="sample:old.md",
        project="event-ingestion",
        access_groups=["engineering"],
        authority=Authority.APPROVED,
        title="Old guidance",
        body="Old body",
        citation=Citation(repository="sample", path="old.md", revision="rev-1"),
    )
    current = KnowledgeChunk(
        id="sample:current.md",
        project="event-ingestion",
        access_groups=["engineering"],
        authority=Authority.APPROVED,
        title="Current guidance",
        body="Use the vendor event identifier.",
        citation=Citation(repository="sample", path="current.md", revision="rev-2"),
    )

    asyncio.run(repository.synchronize("sample", "rev-1", [old]))
    asyncio.run(repository.synchronize("sample", "rev-2", [current]))

    assert set(database.collections["documents"].documents) == {"sample:current.md"}
    assert set(database.collections["document_chunks"].documents) == {"sample:current.md"}
    stored = database.collections["document_chunks"].documents["sample:current.md"]
    assert stored["citation"] == {
        "repository": "sample",
        "path": "current.md",
        "revision": "rev-2",
    }
    revision = database.collections["source_revisions"].documents["sample:rev-2"]
    assert revision["status"] == "ready"
    assert revision["chunk_count"] == 1

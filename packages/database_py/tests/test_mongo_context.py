import asyncio
from typing import Any

from team_agent_contracts import (
    Authority,
    Citation,
    KnowledgeSearchRequest,
    Principal,
)
from team_agent_database import MongoContextRepository, MongoKnowledgeIndex
from team_context_core import KnowledgeChunk


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    async def to_list(self, *, length: int | None) -> list[dict[str, Any]]:
        assert length is None
        return self._documents

    def sort(self, key: str, direction: int) -> "_Cursor":
        assert (key, direction) == ("_id", 1)
        self._documents.sort(key=lambda item: item["_id"])
        return self

    def limit(self, value: int) -> "_Cursor":
        self._documents = self._documents[:value]
        return self


class _Chunks:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents
        self.query: dict[str, Any] | None = None

    def find(self, query: dict[str, Any]) -> _Cursor:
        self.query = query
        return _Cursor(self._documents)


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        actual = document.get(key)
        if isinstance(expected, dict) and "$in" in expected:
            candidates = actual if isinstance(actual, list) else [actual]
            if not set(candidates).intersection(expected["$in"]):
                return False
        elif isinstance(expected, dict) and "$gt" in expected:
            if not isinstance(actual, str) or actual <= expected["$gt"]:
                return False
        elif isinstance(actual, list):
            if expected not in actual:
                return False
        elif actual != expected:
            return False
    return True


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
        for key in update.get("$unset", {}):
            document.pop(key, None)

    async def update_many(self, query: dict[str, Any], update: dict[str, Any]) -> None:
        for document in self.documents.values():
            if _matches(document, query):
                document.update(update.get("$set", {}))
                for key in update.get("$unset", {}):
                    document.pop(key, None)

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

    def find(self, query: dict[str, Any]) -> _Cursor:
        return _Cursor(
            [document for document in self.documents.values() if _matches(document, query)]
        )

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return next(
            (document for document in self.documents.values() if _matches(document, query)),
            None,
        )


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

    async def command(self, command: dict[str, Any]) -> dict[str, int]:
        self.validators[command["collMod"]] = command["validator"]
        return {"ok": 1}

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
        "audit_events",
        "document_chunks",
        "documents",
        "memories",
        "memory_idempotency",
        "memory_proposals",
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
    assert database.collections["memories"].indexes[0][1] == "authoritative_memory_scope"
    assert database.collections["audit_events"].indexes[0][1] == "memory_audit_order"
    proposal_schema = database.validators["memory_proposals"]["$jsonSchema"]["properties"]
    assert proposal_schema["provenance"]["additionalProperties"] is False
    assert proposal_schema["provenance"]["properties"]["repository"]["minLength"] == 1
    assert proposal_schema["provenance"]["properties"]["path"]["minLength"] == 1
    assert proposal_schema["provenance"]["properties"]["revision"]["minLength"] == 1
    assert proposal_schema["evidence"]["minItems"] == 1
    assert proposal_schema["evidence"]["maxItems"] == 20
    assert proposal_schema["evidence"]["items"]["required"] == [
        "repository",
        "path",
        "revision",
    ]
    memory_schema = database.validators["memories"]["$jsonSchema"]["properties"]
    assert memory_schema["evidence"]["minItems"] == 1
    assert memory_schema["evidence"]["maxItems"] == 20
    receipt_schema = database.validators["memory_idempotency"]["$jsonSchema"]["properties"]
    assert "response_json" in receipt_schema
    assert "response" not in receipt_schema


def test_migration_upgrades_all_v2_revisions_without_using_legacy_skills() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    revisions = database.collections["source_revisions"]
    revisions.documents = {
        "sample:rev-1": {
            "_id": "sample:rev-1",
            "schema_version": 2,
            "source_id": "sample",
            "revision": "rev-1",
            "status": "ready",
            "chunk_count": 1,
            "skill_count": 2,
        },
        "sample:rev-2": {
            "_id": "sample:rev-2",
            "schema_version": 2,
            "source_id": "sample",
            "revision": "rev-2",
            "status": "ready",
            "chunk_count": 3,
            "skill_count": 4,
        },
    }
    legacy_skills = _MigrationCollection()
    legacy_skills.documents["legacy"] = {"_id": "legacy", "body": "unused"}
    database.collections["skills"] = legacy_skills

    asyncio.run(repository.migrate())

    assert all(row["schema_version"] == 3 for row in revisions.documents.values())
    assert all("skill_count" not in row for row in revisions.documents.values())
    assert database.collections["skills"].documents == {
        "legacy": {"_id": "legacy", "body": "unused"}
    }


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

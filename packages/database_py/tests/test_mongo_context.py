import asyncio
from typing import Any

from team_agent_contracts import (
    Authority,
    Citation,
    KnowledgeSearchRequest,
    Principal,
    SkillListRequest,
)
from team_agent_database import MongoContextRepository, MongoKnowledgeIndex, MongoSkillCatalog
from team_context_core import KnowledgeChunk, SkillPackage


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


class _Skills(_Chunks):
    def find(self, query: dict[str, Any]) -> _Cursor:
        self.query = query
        matches = [document for document in self._documents if _matches(document, query)]
        return _Cursor(matches)

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        self.query = query
        return next((document for document in self._documents if _matches(document, query)), None)


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


def test_skill_catalog_filters_storage_before_listing_or_loading() -> None:
    documents = [
        {
            "_id": "sample:skills/diagnose/SKILL.md",
            "source_id": "sample",
            "name": "diagnose",
            "description": "Diagnose an incident",
            "version": "1",
            "projects": ["event-ingestion"],
            "access_groups": ["engineering"],
            "allowed_tools": ["search_team_knowledge"],
            "body": "# Diagnose",
            "citation": {
                "repository": "sample",
                "path": "skills/diagnose/SKILL.md",
                "revision": "abc123",
            },
        }
    ]
    collection = _Skills(documents)
    catalog = MongoSkillCatalog(collection, source_id="sample")
    principal = Principal(id="dev", groups=["engineering"], projects=["event-ingestion"])

    items, next_cursor = asyncio.run(
        catalog.list(SkillListRequest(project="event-ingestion", limit=10), principal)
    )

    assert collection.query == {
        "source_id": "sample",
        "projects": "event-ingestion",
        "access_groups": {"$in": ["engineering"]},
    }
    assert [item.name for item in items] == ["diagnose"]
    assert next_cursor is None

    detail = asyncio.run(catalog.get("diagnose", "event-ingestion", principal))
    assert collection.query == {
        "source_id": "sample",
        "name": "diagnose",
        "projects": "event-ingestion",
        "access_groups": {"$in": ["engineering"]},
    }
    assert detail is not None
    assert detail.body == "# Diagnose"
    assert detail.citation.revision == "abc123"


def test_skill_catalog_binding_prevents_cross_source_list_get_and_cursor_reuse() -> None:
    def skill(source: str, name: str, revision: str) -> SkillPackage:
        return SkillPackage(
            id=f"{source}:skills/{name}/SKILL.md",
            name=name,
            description=f"{source} {name}",
            version="1",
            projects=["event-ingestion"],
            access_groups=["engineering"],
            allowed_tools=[],
            body=f"# {name.title()}",
            citation=Citation(
                repository=source,
                path=f"skills/{name}/SKILL.md",
                revision=revision,
            ),
        )

    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    asyncio.run(
        repository.synchronize(
            "source-a", "a-revision", [], [skill("source-a", "alpha", "a-revision")]
        )
    )
    asyncio.run(
        repository.synchronize(
            "source-b",
            "b-revision",
            [],
            [
                skill("source-b", "bravo", "b-revision"),
                skill("source-b", "charlie", "b-revision"),
            ],
        )
    )
    collection = database.collections["skills"]
    source_a = MongoSkillCatalog(collection, source_id="source-a")
    source_b = MongoSkillCatalog(collection, source_id="source-b")
    principal = Principal(id="dev", groups=["engineering"], projects=["event-ingestion"])

    source_a_items, _ = asyncio.run(
        source_a.list(SkillListRequest(project="event-ingestion"), principal)
    )
    source_b_first, source_b_cursor = asyncio.run(
        source_b.list(SkillListRequest(project="event-ingestion", limit=1), principal)
    )
    cross_source_items, _ = asyncio.run(
        source_a.list(
            SkillListRequest(project="event-ingestion", limit=1, cursor=source_b_cursor),
            principal,
        )
    )
    source_b_detail = asyncio.run(source_a.get("bravo", "event-ingestion", principal))

    assert [item.name for item in source_a_items] == ["alpha"]
    assert [item.name for item in source_b_first] == ["bravo"]
    assert source_b_cursor is not None
    assert cross_source_items == []
    assert source_b_detail is None


def test_migration_creates_validated_context_collections_and_indexes() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)

    asyncio.run(repository.migrate())

    assert set(database.collections) == {
        "document_chunks",
        "documents",
        "schema_migrations",
        "skills",
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
    assert database.collections["skills"].indexes == [
        (
            [("projects", 1), ("name", 1), ("_id", 1)],
            "authorized_project_name",
            False,
        ),
        (
            [("access_groups", 1), ("name", 1), ("_id", 1)],
            "authorized_group_name",
            False,
        ),
        ([("source_id", 1), ("name", 1)], "source_skill_name_unique", True),
        ([("source_id", 1), ("citation.revision", 1)], "skill_source_revision", False),
    ]


def test_synchronize_persists_and_prunes_revision_pinned_skills() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    old = SkillPackage(
        id="sample:skills/old/SKILL.md",
        name="old",
        description="Old skill",
        version="1",
        projects=["event-ingestion"],
        access_groups=["engineering"],
        allowed_tools=[],
        body="# Old",
        citation=Citation(repository="sample", path="skills/old/SKILL.md", revision="rev-1"),
    )
    current = old.model_copy(
        update={
            "id": "sample:skills/current/SKILL.md",
            "name": "current",
            "body": "# Current",
            "citation": Citation(
                repository="sample", path="skills/current/SKILL.md", revision="rev-2"
            ),
        }
    )

    asyncio.run(repository.synchronize("sample", "rev-1", [], [old]))
    asyncio.run(repository.synchronize("sample", "rev-2", [], [current]))

    assert set(database.collections["skills"].documents) == {current.id}
    stored = database.collections["skills"].documents[current.id]
    assert stored["body"] == "# Current"
    assert stored["citation"]["revision"] == "rev-2"
    revision = database.collections["source_revisions"].documents["sample:rev-2"]
    assert revision["skill_count"] == 1


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

    asyncio.run(repository.synchronize("sample", "rev-1", [old], []))
    asyncio.run(repository.synchronize("sample", "rev-2", [current], []))

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

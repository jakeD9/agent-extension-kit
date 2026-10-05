import asyncio
from typing import Any

import pytest
from team_agent_contracts import (
    Citation,
    KnowledgeSearchRequest,
    Principal,
)
from team_agent_database import (
    MAX_KNOWLEDGE_CANDIDATES,
    MongoContextRepository,
    MongoKnowledgeIndex,
    ProjectionActivationError,
    ProjectionActivationUncertainError,
)
from team_context_core import KnowledgeChunk


class _Cursor:
    def __init__(self, documents: list[dict[str, Any]]) -> None:
        self._documents = documents

    async def to_list(self, *, length: int | None) -> list[dict[str, Any]]:
        return self._documents if length is None else self._documents[:length]

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
        return _Cursor([document for document in self._documents if _matches(document, query)])


def _matches(document: dict[str, Any], query: dict[str, Any]) -> bool:
    for key, expected in query.items():
        if key == "$or":
            if not any(_matches(document, candidate) for candidate in expected):
                return False
            continue
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

    async def drop_index(self, name: str) -> None:
        self.indexes = [index for index in self.indexes if index[1] != name]

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
    ) -> Any:
        matched = next(
            (key for key, value in self.documents.items() if _matches(value, query)), None
        )
        if matched is not None:
            del self.documents[matched]
            self.documents[replacement["_id"]] = replacement
        elif upsert:
            self.documents[replacement["_id"]] = replacement
        return type("Result", (), {"matched_count": int(matched is not None)})()

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.documents[document["_id"]] = document

    async def count_documents(self, query: dict[str, Any], *, limit: int = 0) -> int:
        count = sum(_matches(document, query) for document in self.documents.values())
        return min(count, limit) if limit else count

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

    async def find_one(
        self, query: dict[str, Any], _projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
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


class _RacingActivationCollection(_MigrationCollection):
    async def replace_one(
        self,
        query: dict[str, Any],
        replacement: dict[str, Any],
        *,
        upsert: bool = False,
    ) -> Any:
        self.documents[query["_id"]] = {
            **replacement,
            "revision": "newer-revision",
        }
        return type("Result", (), {"matched_count": 0})()


class _AmbiguousInsertCollection(_MigrationCollection):
    async def insert_one(self, document: dict[str, Any]) -> None:
        self.documents[document["_id"]] = document
        raise RuntimeError("connection dropped after write")


class _UnreconcilableInsertCollection(_MigrationCollection):
    def __init__(self) -> None:
        super().__init__()
        self._write_applied = False

    async def insert_one(self, document: dict[str, Any]) -> None:
        self.documents[document["_id"]] = document
        self._write_applied = True
        raise RuntimeError("connection dropped after write")

    async def find_one(
        self, query: dict[str, Any], _projection: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        if self._write_applied:
            raise RuntimeError("database unavailable during reconciliation")
        return await super().find_one(query, _projection)


def test_search_filters_storage_candidates_before_scoring() -> None:
    chunks = _Chunks(
        [
            {
                "_id": "approved",
                "logical_id": "approved",
                "source_id": "sample",
                "revision": "abc123",
                "projection_hash": "sample-hash",
                "project": "event-ingestion",
                "title": "Stable idempotency keys",
                "body": "Use the vendor event identifier as the idempotency key.",
                "citation": {
                    "repository": "sample",
                    "path": "knowledge/architecture/idempotency.md",
                    "revision": "abc123",
                    "heading": "Stable event identity",
                },
            },
            {
                "_id": "unrelated",
                "logical_id": "unrelated",
                "source_id": "other",
                "revision": "secret",
                "project": "event-ingestion",
                "title": "Unrelated domain",
                "body": "vendor idempotency should not cross source ownership",
                "citation": {
                    "repository": "other",
                    "path": "secret.md",
                    "revision": "secret",
                },
            },
        ]
    )
    active = _Chunks(
        [
            {
                "_id": "sample",
                "source_id": "sample",
                "revision": "abc123",
                "projection_hash": "sample-hash",
            },
            {
                "_id": "other",
                "source_id": "other",
                "revision": "secret",
                "projection_hash": "other-hash",
            },
        ]
    )
    index = MongoKnowledgeIndex(chunks, active, "sample")

    results = asyncio.run(
        index.search(
            KnowledgeSearchRequest(query="vendor idempotency", project="event-ingestion"),
            Principal(id="dev", groups=["engineering"], projects=["event-ingestion"]),
        )
    )

    assert chunks.query == {
        "project": "event-ingestion",
        "source_id": "sample",
        "revision": "abc123",
        "projection_hash": "sample-hash",
    }
    assert [result.id for result in results] == ["approved"]
    assert results[0].citation.revision == "abc123"


def test_migration_creates_validated_context_collections_and_indexes() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)

    asyncio.run(repository.migrate())

    assert set(database.collections) == {
        "active_knowledge_revisions",
        "knowledge_chunks",
        "knowledge_documents",
        "knowledge_revisions",
        "schema_migrations",
        "shared_memories",
        "shared_memory_audit_events",
        "shared_memory_idempotency",
    }
    assert set(database.validators) == set(database.collections)
    assert all(
        validator["$jsonSchema"]["additionalProperties"] is False
        for validator in database.validators.values()
    )
    assert database.collections["knowledge_chunks"].indexes == [
        (
            [
                ("project", 1),
                ("source_id", 1),
                ("revision", 1),
                ("projection_hash", 1),
            ],
            "project_source_revision_projection",
            False,
        ),
        ([("source_id", 1), ("citation.revision", 1)], "source_revision", False),
    ]
    assert database.collections["knowledge_revisions"].indexes == [
        (
            [("source_id", 1), ("revision", 1), ("projection_hash", 1)],
            "source_revision_projection_unique",
            True,
        )
    ]
    assert database.collections["shared_memories"].indexes[0][1] == "supplemental_memory_project"
    assert database.collections["shared_memory_audit_events"].indexes[0][1] == "memory_audit_order"
    memory_schema = database.validators["shared_memories"]["$jsonSchema"]["properties"]
    assert memory_schema["provenance"]["additionalProperties"] is False
    assert memory_schema["provenance"]["properties"]["repository"]["minLength"] == 1
    assert memory_schema["evidence"]["minItems"] == 1
    assert memory_schema["evidence"]["maxItems"] == 20
    assert memory_schema["evidence"]["items"]["required"] == [
        "repository",
        "path",
        "revision",
    ]
    receipt_schema = database.validators["shared_memory_idempotency"]["$jsonSchema"]["properties"]
    assert "response_json" in receipt_schema
    assert "response" not in receipt_schema


def test_migration_leaves_legacy_projection_collections_untouched() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    legacy_revisions = _MigrationCollection()
    legacy_revisions.documents = {
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
    database.collections["source_revisions"] = legacy_revisions

    asyncio.run(repository.migrate())

    assert all(row["schema_version"] == 2 for row in legacy_revisions.documents.values())
    assert all("skill_count" in row for row in legacy_revisions.documents.values())


def test_v5_migration_fails_closed_without_deleting_legacy_governed_memory() -> None:
    database = _MigrationDatabase()
    legacy = _MigrationCollection()
    legacy.documents["proposal-1"] = {"_id": "proposal-1", "status": "approved"}
    database.collections["memory_proposals"] = legacy

    with pytest.raises(RuntimeError, match="cannot be reinterpreted"):
        asyncio.run(MongoContextRepository(database).migrate())

    assert legacy.documents == {"proposal-1": {"_id": "proposal-1", "status": "approved"}}


def test_synchronize_stages_revisions_and_atomically_activates_latest() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    old = KnowledgeChunk(
        id="sample:old.md",
        project="event-ingestion",
        title="Old guidance",
        body="Old body",
        citation=Citation(repository="sample", path="old.md", revision="rev-1"),
    )
    current = KnowledgeChunk(
        id="sample:current.md",
        project="event-ingestion",
        title="Current guidance",
        body="Use the vendor event identifier.",
        citation=Citation(repository="sample", path="current.md", revision="rev-2"),
    )

    asyncio.run(repository.synchronize("sample", "rev-1", [old]))
    asyncio.run(
        repository.synchronize("sample", "rev-2", [current], expected_active_revision="rev-1")
    )

    documents = list(database.collections["knowledge_chunks"].documents.values())
    assert {document["logical_id"] for document in documents} == {
        "sample:old.md",
        "sample:current.md",
    }
    assert (
        database.collections["active_knowledge_revisions"].documents["sample"]["revision"]
        == "rev-2"
    )
    stored = next(
        document for document in documents if document["logical_id"] == "sample:current.md"
    )
    assert stored["citation"] == {
        "repository": "sample",
        "path": "current.md",
        "revision": "rev-2",
    }
    revision = next(
        row
        for row in database.collections["knowledge_revisions"].documents.values()
        if row["revision"] == "rev-2"
    )
    assert revision["status"] == "ready"
    assert revision["chunk_count"] == 1


def test_failed_sync_keeps_prior_complete_revision_active() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    old = KnowledgeChunk(
        id="old",
        project="project",
        title="Old",
        body="old complete content",
        citation=Citation(repository="repo", path="old.md", revision="rev-1"),
    )
    asyncio.run(repository.synchronize("source", "rev-1", [old]))
    invalid = old.model_copy(
        update={"citation": old.citation.model_copy(update={"revision": "wrong"})}
    )

    with pytest.raises(ValueError, match="does not match"):
        asyncio.run(
            repository.synchronize("source", "rev-2", [invalid], expected_active_revision="rev-1")
        )

    assert (
        database.collections["active_knowledge_revisions"].documents["source"]["revision"]
        == "rev-1"
    )


def test_stale_activation_cannot_replace_concurrent_winner() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    first = KnowledgeChunk(
        id="guide",
        project="project",
        title="First",
        body="first",
        citation=Citation(repository="repo", path="guide.md", revision="rev-1"),
    )
    asyncio.run(repository.synchronize("source", "rev-1", [first]))
    racing = _RacingActivationCollection()
    racing.documents = dict(database.collections["active_knowledge_revisions"].documents)
    database.collections["active_knowledge_revisions"] = racing
    second = first.model_copy(
        update={"citation": first.citation.model_copy(update={"revision": "rev-2"})}
    )

    with pytest.raises(ProjectionActivationError):
        asyncio.run(
            repository.synchronize("source", "rev-2", [second], expected_active_revision="rev-1")
        )

    assert racing.documents["source"]["revision"] == "newer-revision"


def test_stale_sequential_publication_requires_expected_predecessor() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    newer = KnowledgeChunk(
        id="guide",
        project="project",
        title="Newer",
        body="newer",
        citation=Citation(repository="repo", path="guide.md", revision="newer"),
    )
    asyncio.run(repository.synchronize("source", "newer", [newer]))
    stale = newer.model_copy(
        update={"citation": newer.citation.model_copy(update={"revision": "stale"})}
    )

    with pytest.raises(ProjectionActivationError, match="expected predecessor"):
        asyncio.run(repository.synchronize("source", "stale", [stale]))

    assert (
        database.collections["active_knowledge_revisions"].documents["source"]["revision"]
        == "newer"
    )
    assert not any(
        row.get("revision") == "stale"
        for row in database.collections["knowledge_revisions"].documents.values()
    )


def test_ambiguous_activation_ack_reconciles_candidate_as_ready() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    ambiguous = _AmbiguousInsertCollection()
    database.collections["active_knowledge_revisions"] = ambiguous
    chunk = KnowledgeChunk(
        id="guide",
        project="project",
        title="Guide",
        body="complete",
        citation=Citation(repository="repo", path="guide.md", revision="rev-1"),
    )

    asyncio.run(repository.synchronize("source", "rev-1", [chunk]))

    assert ambiguous.documents["source"]["revision"] == "rev-1"
    assert (
        next(iter(database.collections["knowledge_revisions"].documents.values()))["status"]
        == "ready"
    )


def test_ambiguous_activation_with_failed_reread_is_never_marked_failed() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    ambiguous = _UnreconcilableInsertCollection()
    database.collections["active_knowledge_revisions"] = ambiguous
    chunk = KnowledgeChunk(
        id="guide",
        project="project",
        title="Guide",
        body="complete",
        citation=Citation(repository="repo", path="guide.md", revision="rev-1"),
    )

    with pytest.raises(ProjectionActivationUncertainError):
        asyncio.run(repository.synchronize("source", "rev-1", [chunk]))

    assert ambiguous.documents["source"]["revision"] == "rev-1"
    revision = next(iter(database.collections["knowledge_revisions"].documents.values()))
    assert revision["status"] == "activation_uncertain"


def test_mutable_same_revision_republishes_changed_same_count_content() -> None:
    database = _MigrationDatabase()
    repository = MongoContextRepository(database)
    asyncio.run(repository.migrate())
    old = KnowledgeChunk(
        id="guide",
        project="project",
        title="Guide",
        body="old content",
        citation=Citation(repository="repo", path="guide.md", revision="dev"),
    )
    updated = old.model_copy(update={"body": "new content"})
    asyncio.run(repository.synchronize("source", "dev", [old]))
    old_hash = database.collections["active_knowledge_revisions"].documents["source"][
        "projection_hash"
    ]

    asyncio.run(
        repository.synchronize("source", "dev", [updated], allow_same_revision_republish=True)
    )

    active = database.collections["active_knowledge_revisions"].documents["source"]
    assert active["projection_hash"] != old_hash
    results = asyncio.run(
        MongoKnowledgeIndex(
            database.collections["knowledge_chunks"],
            database.collections["active_knowledge_revisions"],
            "source",
        ).search(
            KnowledgeSearchRequest(query="content", project="project"),
            Principal(id="dev", projects=["project"]),
        )
    )
    assert [result.excerpt for result in results] == ["new content"]


def test_search_reads_only_active_revision_and_bounds_candidates() -> None:
    old = {
        "_id": "old",
        "logical_id": "guide",
        "source_id": "source",
        "revision": "rev-1",
        "projection_hash": "old-hash",
        "project": "project",
        "title": "Old retry guidance",
        "body": "use old retry behavior",
        "citation": {"repository": "repo", "path": "guide.md", "revision": "rev-1"},
    }
    current = {
        **old,
        "_id": "current",
        "revision": "rev-2",
        "projection_hash": "current-hash",
        "title": "Current retry guidance",
        "body": "use current retry behavior",
        "citation": {"repository": "repo", "path": "guide.md", "revision": "rev-2"},
    }
    active = _Chunks(
        [
            {
                "_id": "source",
                "source_id": "source",
                "revision": "rev-2",
                "projection_hash": "current-hash",
            }
        ]
    )
    principal = Principal(id="dev", projects=["project"])
    results = asyncio.run(
        MongoKnowledgeIndex(_Chunks([old, current]), active, "source").search(
            KnowledgeSearchRequest(query="retry behavior", project="project"), principal
        )
    )
    assert [result.title for result in results] == ["Current retry guidance"]

    too_many = _Chunks([current.copy() for _ in range(MAX_KNOWLEDGE_CANDIDATES + 1)])
    with pytest.raises(RuntimeError, match="candidate count"):
        asyncio.run(
            MongoKnowledgeIndex(too_many, active, "source").search(
                KnowledgeSearchRequest(query="retry", project="project"), principal
            )
        )

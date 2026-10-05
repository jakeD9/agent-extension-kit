import asyncio
from pathlib import Path
from typing import Any

from team_agent_database import ProjectionActivationError, knowledge_projection_hash
from team_context_service import load_content_pack
from team_context_service.persistence import MongoContextBackend

EXTENSION_PATH = Path(__file__).parents[3] / "extension"
DEFAULT_PACK = load_content_pack(EXTENSION_PATH, "fixture-revision")
DEFAULT_PROJECTION_HASH = knowledge_projection_hash(DEFAULT_PACK.chunks)


class _Client:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Revisions:
    def __init__(self, document: dict[str, Any] | None) -> None:
        self.document = document

    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        if self.document is None:
            return None
        return (
            self.document
            if all(self.document.get(key) == value for key, value in query.items())
            else None
        )


class _Database:
    def __init__(
        self,
        revision: str | None = None,
        chunk_count: int = 3,
        projection_hash: str = DEFAULT_PROJECTION_HASH,
    ) -> None:
        self.commands: list[dict[str, int]] = []
        self.revision = revision
        self.chunk_count = chunk_count
        self.projection_hash = projection_hash

    async def command(self, command: dict[str, int]) -> dict[str, int]:
        self.commands.append(command)
        return {"ok": 1}

    def __getitem__(self, name: str) -> _Revisions:
        if self.revision is None:
            return _Revisions(None)
        if name == "active_knowledge_revisions":
            return _Revisions(
                {
                    "_id": "agent-extension-kit-sample",
                    "revision": self.revision,
                    "projection_hash": self.projection_hash,
                    "chunk_count": self.chunk_count,
                }
            )
        assert name == "knowledge_revisions"
        return _Revisions(
            {
                "source_id": "agent-extension-kit-sample",
                "revision": self.revision,
                "projection_hash": self.projection_hash,
                "status": "ready",
                "chunk_count": self.chunk_count,
            }
        )


class _Repository:
    def __init__(self, error: Exception | None = None) -> None:
        self.migrated = False
        self.synchronized: tuple[str, str, int] | None = None
        self.error = error
        self.synchronize_attempts = 0

    async def migrate(self) -> None:
        self.migrated = True

    async def synchronize(
        self,
        source_id: str,
        revision: str,
        chunks: list[Any],
        *,
        expected_active_revision: str | None = None,
        allow_same_revision_republish: bool = False,
    ) -> None:
        self.synchronize_attempts += 1
        if self.error is not None:
            raise self.error
        self.synchronized = (source_id, revision, len(chunks))


def test_backend_initializes_checks_revision_and_closes_client() -> None:
    client = _Client()
    database = _Database("fixture-revision")
    repository = _Repository()
    pack = DEFAULT_PACK
    backend = MongoContextBackend(client, database, repository, pack, bootstrap_content=True)

    asyncio.run(backend.startup())
    status, details = asyncio.run(backend.readiness())
    asyncio.run(backend.shutdown())

    assert repository.migrated is True
    assert repository.synchronized == (
        "agent-extension-kit-sample",
        "fixture-revision",
        3,
    )
    assert status == "ready"
    assert details == {"mongodb": "ready", "source_revision": "fixture-revision"}
    assert database.commands == [{"ping": 1}, {"ping": 1}]
    assert client.closed is True


def test_backend_uses_explicit_revision_for_an_empty_content_pack() -> None:
    client = _Client()
    database = _Database("empty-revision", chunk_count=0)
    repository = _Repository()
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision").model_copy(
        update={"revision": "empty-revision", "chunks": [], "skills": []}
    )
    database.projection_hash = knowledge_projection_hash(pack.chunks)
    backend = MongoContextBackend(client, database, repository, pack, bootstrap_content=True)

    asyncio.run(backend.startup())

    assert repository.synchronized == (
        "agent-extension-kit-sample",
        "empty-revision",
        0,
    )


def test_backend_without_bootstrap_rejects_missing_or_mismatched_projection() -> None:
    pack = DEFAULT_PACK
    for revision in (None, "different-revision"):
        backend = MongoContextBackend(_Client(), _Database(revision), _Repository(), pack)
        asyncio.run(backend.startup())
        status, details = asyncio.run(backend.readiness())
        assert status == "not_ready"
        assert details == {
            "mongodb": "ready",
            "source_revision": "not_ready",
        }


def test_bootstrap_cas_loss_is_sticky_and_never_serves_mismatched_skills() -> None:
    pack = DEFAULT_PACK
    repository = _Repository(ProjectionActivationError("newer revision won"))
    backend = MongoContextBackend(
        _Client(),
        _Database("newer-revision"),
        repository,
        pack,
        bootstrap_content=True,
    )

    asyncio.run(backend.startup())
    first = asyncio.run(backend.readiness())
    second = asyncio.run(backend.readiness())

    assert (
        first
        == second
        == (
            "not_ready",
            {"mongodb": "ready", "source_revision": "not_ready"},
        )
    )
    assert repository.synchronize_attempts == 1

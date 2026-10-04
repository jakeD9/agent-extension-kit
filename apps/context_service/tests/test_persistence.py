import asyncio
from pathlib import Path
from typing import Any

from team_context_service import load_content_pack
from team_context_service.persistence import MongoContextBackend

EXTENSION_PATH = Path(__file__).parents[3] / "extension"


class _Client:
    def __init__(self) -> None:
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class _Revisions:
    async def find_one(self, query: dict[str, Any]) -> dict[str, Any] | None:
        return {
            "source_id": query["source_id"],
            "revision": query["revision"],
            "status": "ready",
            "chunk_count": 3,
        }


class _Database:
    def __init__(self) -> None:
        self.commands: list[dict[str, int]] = []

    async def command(self, command: dict[str, int]) -> dict[str, int]:
        self.commands.append(command)
        return {"ok": 1}

    def __getitem__(self, name: str) -> _Revisions:
        assert name == "source_revisions"
        return _Revisions()


class _Repository:
    def __init__(self) -> None:
        self.migrated = False
        self.synchronized: tuple[str, str, int] | None = None

    async def migrate(self) -> None:
        self.migrated = True

    async def synchronize(self, source_id: str, revision: str, chunks: list[Any]) -> None:
        self.synchronized = (source_id, revision, len(chunks))


def test_backend_initializes_checks_revision_and_closes_client() -> None:
    client = _Client()
    database = _Database()
    repository = _Repository()
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")
    backend = MongoContextBackend(client, database, repository, pack)

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
    database = _Database()
    repository = _Repository()
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision").model_copy(
        update={"revision": "empty-revision", "chunks": [], "skills": []}
    )
    backend = MongoContextBackend(client, database, repository, pack)

    asyncio.run(backend.startup())

    assert repository.synchronized == (
        "agent-extension-kit-sample",
        "empty-revision",
        0,
    )

import asyncio
from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from team_context_core import KnowledgeChunk

from team_context_service.content_pack import ContentPack


class _Client(Protocol):
    async def close(self) -> None: ...


class _RevisionCollection(Protocol):
    async def find_one(self, filter: Mapping[str, Any]) -> Mapping[str, Any] | None: ...


class ContextDatabase(Protocol):
    async def command(self, command: Mapping[str, Any]) -> Mapping[str, Any]: ...

    def __getitem__(self, name: str) -> _RevisionCollection: ...


class _Repository(Protocol):
    async def migrate(self) -> None: ...

    async def synchronize(
        self,
        source_id: str,
        revision: str,
        chunks: Sequence[KnowledgeChunk],
    ) -> None: ...


class MongoContextBackend:
    """Coordinates context schema, Git content synchronization, and health state."""

    def __init__(
        self,
        client: _Client,
        database: ContextDatabase,
        repository: _Repository,
        pack: ContentPack,
    ) -> None:
        self._client = client
        self._database = database
        self._repository = repository
        self._pack = pack
        self._initialized = False
        self._initialization_lock = asyncio.Lock()

    @property
    def _revision(self) -> str:
        return self._pack.revision

    async def _initialize(self) -> None:
        async with self._initialization_lock:
            if self._initialized:
                return
            try:
                await self._database.command({"ping": 1})
                await self._repository.migrate()
                await self._repository.synchronize(
                    self._pack.manifest.id,
                    self._revision,
                    self._pack.chunks,
                )
            except Exception:
                self._initialized = False
            else:
                self._initialized = True

    async def startup(self) -> None:
        await self._initialize()

    async def readiness(self) -> tuple[str, dict[str, object] | None]:
        if not self._initialized:
            await self._initialize()
        if not self._initialized:
            return "not_ready", {
                "mongodb": "unavailable",
                "source_revision": self._revision,
            }
        try:
            await self._database.command({"ping": 1})
            revision = await self._database["source_revisions"].find_one(
                {
                    "source_id": self._pack.manifest.id,
                    "revision": self._revision,
                    "status": "ready",
                }
            )
        except Exception:
            return "not_ready", {
                "mongodb": "unavailable",
                "source_revision": self._revision,
            }
        if revision is None or revision.get("chunk_count") != len(self._pack.chunks):
            return "not_ready", {
                "mongodb": "ready",
                "source_revision": "not_ready",
            }
        return "ready", {"mongodb": "ready", "source_revision": self._revision}

    async def shutdown(self) -> None:
        await self._client.close()


__all__ = ["ContextDatabase", "MongoContextBackend"]

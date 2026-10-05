import asyncio
from collections.abc import Awaitable, Callable, Mapping, Sequence
from typing import Any, Protocol

from team_agent_database import ProjectionActivationError, knowledge_projection_hash
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
        *,
        expected_active_revision: str | None = None,
        allow_same_revision_republish: bool = False,
    ) -> None: ...


class MongoContextBackend:
    """Coordinates context schema, Git content synchronization, and health state."""

    def __init__(
        self,
        client: _Client,
        database: ContextDatabase,
        repository: _Repository,
        pack: ContentPack,
        transaction_verifier: Callable[[], Awaitable[None]] | None = None,
        bootstrap_content: bool = False,
        allow_mutable_content_revision: bool = False,
    ) -> None:
        self._client = client
        self._database = database
        self._repository = repository
        self._pack = pack
        self._transaction_verifier = transaction_verifier
        self._bootstrap_content = bootstrap_content
        self._allow_mutable_content_revision = allow_mutable_content_revision
        self._initialized = False
        self._activation_conflict = False
        self._initialization_lock = asyncio.Lock()

    @property
    def _revision(self) -> str:
        return self._pack.revision

    async def _initialize(self) -> None:
        async with self._initialization_lock:
            if self._initialized or self._activation_conflict:
                return
            try:
                await self._database.command({"ping": 1})
                await self._repository.migrate()
                if self._transaction_verifier is not None:
                    await self._transaction_verifier()
                if self._bootstrap_content:
                    await self._repository.synchronize(
                        self._pack.manifest.id,
                        self._revision,
                        self._pack.chunks,
                        expected_active_revision=None,
                        allow_same_revision_republish=self._allow_mutable_content_revision,
                    )
                if not await self._projection_is_ready():
                    raise RuntimeError("The active knowledge projection does not match Git content")
            except ProjectionActivationError:
                self._initialized = False
                self._activation_conflict = True
            except Exception:
                self._initialized = False
            else:
                self._initialized = True

    async def startup(self) -> None:
        await self._initialize()

    async def _projection_is_ready(self) -> bool:
        projection_hash = knowledge_projection_hash(self._pack.chunks)
        active = await self._database["active_knowledge_revisions"].find_one(
            {"_id": self._pack.manifest.id}
        )
        if (
            active is None
            or active.get("revision") != self._revision
            or active.get("projection_hash") != projection_hash
            or active.get("chunk_count") != len(self._pack.chunks)
        ):
            return False
        revision = await self._database["knowledge_revisions"].find_one(
            {
                "source_id": self._pack.manifest.id,
                "revision": self._revision,
                "projection_hash": projection_hash,
                "status": "ready",
            }
        )
        return revision is not None and revision.get("chunk_count") == len(self._pack.chunks)

    async def readiness(self) -> tuple[str, dict[str, object] | None]:
        if not self._initialized:
            await self._initialize()
        if not self._initialized:
            try:
                await self._database.command({"ping": 1})
            except Exception:
                return "not_ready", {
                    "mongodb": "unavailable",
                    "source_revision": self._revision,
                }
            return "not_ready", {
                "mongodb": "ready",
                "source_revision": "not_ready",
            }
        try:
            await self._database.command({"ping": 1})
            projection_ready = await self._projection_is_ready()
        except Exception:
            return "not_ready", {
                "mongodb": "unavailable",
                "source_revision": self._revision,
            }
        if not projection_ready:
            return "not_ready", {
                "mongodb": "ready",
                "source_revision": "not_ready",
            }
        return "ready", {"mongodb": "ready", "source_revision": self._revision}

    async def shutdown(self) -> None:
        await self._client.close()


__all__ = ["ContextDatabase", "MongoContextBackend"]

import base64
import json
import re
import unicodedata
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from hashlib import sha256
from typing import Protocol
from uuid import uuid4

from pydantic import BaseModel, ConfigDict
from team_agent_contracts import (
    Citation,
    ImmutableSkillPackageManifest,
    KnowledgeResult,
    KnowledgeSearchRequest,
    MemoryAuditAction,
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
    SkillDetail,
    SkillListRequest,
    SkillPackageBundle,
    SkillResolutionManifest,
    SkillResolveRequest,
    SkillSummary,
    TeamMemory,
)


class KnowledgeChunk(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    project: str
    title: str
    body: str
    citation: Citation


class SkillPackage(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str
    description: str
    version: str
    projects: list[str]
    allowed_tools: list[str]
    body: str
    citation: Citation
    package_manifest: ImmutableSkillPackageManifest | None = None
    bundle: SkillPackageBundle | None = None


class KnowledgeIndex(Protocol):
    async def search(
        self, request: KnowledgeSearchRequest, principal: Principal
    ) -> list[KnowledgeResult]: ...


class InvalidSkillCursor(ValueError):
    pass


class SkillNotFoundError(ValueError):
    pass


class SkillRevisionNotFoundError(ValueError):
    pass


class MemoryForbiddenError(PermissionError):
    pass


class MemoryNotFoundError(LookupError):
    pass


class MemoryConflictError(RuntimeError):
    pass


class InvalidMemoryCursor(ValueError):
    pass


class SharedMemoryStore(Protocol):
    async def create(
        self,
        request: SharedMemoryCreateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> SharedMemoryResponse: ...

    async def update(
        self,
        memory_id: str,
        request: SharedMemoryUpdateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> SharedMemoryResponse: ...

    async def expire(
        self,
        memory_id: str,
        request: MemoryExpireRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> MemoryMutationResponse: ...

    async def search(
        self, request: MemorySearchRequest, principal: Principal
    ) -> tuple[list[MemorySearchItem], str | None]: ...

    async def audit(
        self, request: MemoryAuditListRequest, principal: Principal
    ) -> tuple[list[MemoryAuditEvent], str | None]: ...


class SkillCatalog(Protocol):
    async def list(
        self, request: SkillListRequest, principal: Principal
    ) -> tuple[list[SkillSummary], str | None]: ...

    async def get(
        self,
        name: str,
        project: str,
        principal: Principal,
        revision: str | None = None,
    ) -> SkillDetail | None: ...

    async def resolve(
        self, request: SkillResolveRequest, principal: Principal
    ) -> SkillResolutionManifest: ...

    async def get_package(
        self, package_id: str, project: str, principal: Principal
    ) -> SkillPackageBundle | None: ...


def _tokenize(value: str) -> list[str]:
    normalized = unicodedata.normalize("NFKD", value).lower()
    return [token for token in re.split(r"[^\w-]+", normalized) if len(token) > 1]


class InMemoryKnowledgeIndex:
    def __init__(self, chunks: Sequence[KnowledgeChunk]) -> None:
        self._chunks = tuple(chunks)

    async def search(
        self, request: KnowledgeSearchRequest, principal: Principal
    ) -> list[KnowledgeResult]:
        if request.project not in principal.projects:
            return []

        query_terms = set(_tokenize(request.query))
        scored: list[tuple[KnowledgeChunk, int]] = []
        for chunk in self._chunks:
            if chunk.project != request.project:
                continue
            score = sum(3 for term in _tokenize(chunk.title) if term in query_terms)
            score += sum(1 for term in _tokenize(chunk.body) if term in query_terms)
            if score > 0:
                scored.append((chunk, score))

        scored.sort(key=lambda item: (-item[1], item[0].id))
        return [
            KnowledgeResult(
                id=chunk.id,
                title=chunk.title,
                excerpt=chunk.body[:500],
                score=float(score),
                citation=chunk.citation,
            )
            for chunk, score in scored[: request.limit]
        ]


def encode_skill_cursor(project: str, revision: str, after: str) -> str:
    payload = json.dumps(
        {"v": 1, "project": project, "revision": revision, "after": after},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_skill_cursor(cursor: str, project: str, revision: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.b64decode(cursor + padding, altchars=b"-_", validate=True))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "project", "revision", "after"}
            or payload["v"] != 1
            or payload["project"] != project
            or payload["revision"] != revision
            or not isinstance(payload["after"], str)
            or not payload["after"]
        ):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise InvalidSkillCursor("The skill cursor is invalid") from error
    return payload["after"]


class GitSkillCatalog:
    """Authorized catalog built from skill packages scanned from a pinned Git checkout."""

    def __init__(self, skills: Sequence[SkillPackage], revision: str) -> None:
        if not revision:
            raise ValueError("A Git skill catalog revision is required")
        self._skills = tuple(skills)
        if any(
            skill.citation.revision != revision
            or (
                skill.package_manifest is not None
                and skill.package_manifest.source_revision != revision
            )
            for skill in skills
        ):
            raise ValueError("Every skill must match the Git skill catalog revision")
        self._revision = revision

    @staticmethod
    def _authorized(skill: SkillPackage, project: str, principal: Principal) -> bool:
        return project in principal.projects and project in skill.projects

    async def list(
        self, request: SkillListRequest, principal: Principal
    ) -> tuple[list[SkillSummary], str | None]:
        authorized = sorted(
            (
                skill
                for skill in self._skills
                if self._authorized(skill, request.project, principal)
            ),
            key=lambda skill: skill.name,
        )
        after = (
            decode_skill_cursor(request.cursor, request.project, self._revision)
            if request.cursor
            else None
        )
        if after is not None:
            authorized = [skill for skill in authorized if skill.name > after]
        page = authorized[: request.limit + 1]
        visible = page[: request.limit]
        items = [
            SkillSummary(
                name=skill.name,
                description=skill.description,
                version=skill.version,
                projects=skill.projects,
                allowed_tools=skill.allowed_tools,
                citation=skill.citation,
            )
            for skill in visible
        ]
        next_cursor = None
        if len(page) > request.limit:
            next_cursor = encode_skill_cursor(request.project, self._revision, visible[-1].name)
        return items, next_cursor

    async def get(
        self,
        name: str,
        project: str,
        principal: Principal,
        revision: str | None = None,
    ) -> SkillDetail | None:
        skill = next(
            (
                candidate
                for candidate in self._skills
                if candidate.name == name and self._authorized(candidate, project, principal)
            ),
            None,
        )
        if skill is None:
            return None
        if revision is not None and revision != self._revision:
            raise SkillRevisionNotFoundError("The requested skill revision is unavailable")
        return SkillDetail(
            name=skill.name,
            description=skill.description,
            version=skill.version,
            projects=skill.projects,
            allowed_tools=skill.allowed_tools,
            citation=skill.citation,
            body=skill.body,
        )

    async def resolve(
        self, request: SkillResolveRequest, principal: Principal
    ) -> SkillResolutionManifest:
        if request.project not in principal.projects:
            raise SkillNotFoundError("Skill not found")
        authorized = {
            skill.name: skill
            for skill in self._skills
            if self._authorized(skill, request.project, principal)
        }
        selected_names = sorted(authorized) if request.all else list(request.names or [])
        for name in selected_names:
            if name not in authorized or authorized[name].package_manifest is None:
                raise SkillNotFoundError("Skill not found")
        if request.revision is not None and request.revision != self._revision:
            raise SkillRevisionNotFoundError("The requested skill revision is unavailable")
        package_manifests: list[ImmutableSkillPackageManifest] = []
        for name in selected_names:
            package_manifest = authorized[name].package_manifest
            assert package_manifest is not None
            package_manifests.append(package_manifest)
        return SkillResolutionManifest(
            catalog_revision=self._revision,
            project=request.project,
            selected_names=selected_names,
            packages=package_manifests,
        )

    async def get_package(
        self, package_id: str, project: str, principal: Principal
    ) -> SkillPackageBundle | None:
        skill = next(
            (
                candidate
                for candidate in self._skills
                if candidate.package_manifest is not None
                and candidate.package_manifest.package_id == package_id
                and self._authorized(candidate, project, principal)
            ),
            None,
        )
        return skill.bundle if skill is not None else None


def _encode_memory_cursor(kind: str, binding: str, after: str) -> str:
    payload = json.dumps(
        {"v": 1, "kind": kind, "binding": binding, "after": after},
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def _decode_memory_cursor(cursor: str, kind: str, binding: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.b64decode(cursor + padding, altchars=b"-_", validate=True))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "kind", "binding", "after"}
            or payload["v"] != 1
            or payload["kind"] != kind
            or payload["binding"] != binding
            or not isinstance(payload["after"], str)
            or not payload["after"]
        ):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise InvalidMemoryCursor("The memory cursor is invalid") from error
    return payload["after"]


class InMemorySharedMemory:
    """Reference project-scoped supplemental-memory service."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        memories: Sequence[TeamMemory] = (),
        audits: Sequence[MemoryAuditEvent] = (),
        idempotency: dict[tuple[str, str, str], tuple[str, object]] | None = None,
    ) -> None:
        source_clock = clock or (lambda: datetime.now(UTC))

        def millisecond_clock() -> datetime:
            value = source_clock()
            return value.replace(microsecond=(value.microsecond // 1_000) * 1_000)

        self._clock = millisecond_clock
        self._memories = {memory.id: memory for memory in memories}
        self._audits = list(audits)
        self._idempotency = dict(idempotency or {})

    def snapshot(
        self,
    ) -> tuple[
        list[TeamMemory],
        list[MemoryAuditEvent],
        dict[tuple[str, str, str], tuple[str, object]],
    ]:
        return (
            list(self._memories.values()),
            list(self._audits),
            dict(self._idempotency),
        )

    @staticmethod
    def _fingerprint(value: BaseModel) -> str:
        return sha256(value.model_dump_json(exclude_none=True).encode()).hexdigest()

    def _record(
        self,
        action: MemoryAuditAction,
        actor_id: str,
        memory: TeamMemory,
        idempotency_key: str,
        *,
        reason: str | None = None,
    ) -> None:
        self._audits.append(
            MemoryAuditEvent(
                id=str(uuid4()),
                action=action,
                actor_id=actor_id,
                project=memory.project,
                memory_id=memory.id,
                memory_revision=memory.revision,
                idempotency_key=idempotency_key,
                reason=reason,
                occurred_at=self._clock(),
            )
        )

    async def create(
        self,
        request: SharedMemoryCreateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> SharedMemoryResponse:
        if request.project not in principal.projects:
            raise MemoryForbiddenError("The caller cannot create memory for this project")
        key = (principal.id, "create", idempotency_key)
        fingerprint = self._fingerprint(request)
        prior = self._idempotency.get(key)
        if prior is not None:
            if prior[0] != fingerprint:
                raise MemoryConflictError("The idempotency key was reused with different input")
            assert isinstance(prior[1], SharedMemoryResponse)
            if prior[1].memory.project not in principal.projects:
                raise MemoryNotFoundError("Memory not found")
            return prior[1]
        now = self._clock()
        if request.expires_at <= now:
            raise MemoryConflictError("A shared memory must expire in the future")
        superseded: TeamMemory | None = None
        if request.supersedes_memory_id is not None:
            superseded = self._memories.get(request.supersedes_memory_id)
            if (
                superseded is None
                or superseded.project != request.project
                or superseded.superseded_by_memory_id is not None
                or superseded.expired_at is not None
                or superseded.expires_at <= now
            ):
                raise MemoryConflictError("The superseded memory is not current in this project")
        request_data = request.model_dump()
        expires_at = request.expires_at.replace(
            microsecond=(request.expires_at.microsecond // 1_000) * 1_000
        )
        request_data["expires_at"] = expires_at
        memory = TeamMemory(
            id=str(uuid4()),
            **request_data,
            author_id=principal.id,
            last_modified_by=principal.id,
            created_at=now,
            updated_at=now,
            revision=1,
        )
        self._memories[memory.id] = memory
        if superseded is not None:
            replaced = superseded.model_copy(
                update={
                    "superseded_by_memory_id": memory.id,
                    "last_modified_by": principal.id,
                    "updated_at": now,
                    "revision": superseded.revision + 1,
                }
            )
            self._memories[superseded.id] = replaced
            self._record(MemoryAuditAction.SUPERSEDED, principal.id, replaced, idempotency_key)
        self._record(MemoryAuditAction.CREATED, principal.id, memory, idempotency_key)
        response = SharedMemoryResponse(memory=memory, request_id="")
        self._idempotency[key] = (fingerprint, response)
        return response

    async def update(
        self,
        memory_id: str,
        request: SharedMemoryUpdateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> SharedMemoryResponse:
        key = (principal.id, f"update:{memory_id}", idempotency_key)
        fingerprint = self._fingerprint(request)
        prior = self._idempotency.get(key)
        if prior is not None:
            if prior[0] != fingerprint:
                raise MemoryConflictError("The idempotency key was reused with different input")
            assert isinstance(prior[1], SharedMemoryResponse)
            if prior[1].memory.project not in principal.projects:
                raise MemoryNotFoundError("Memory not found")
            return prior[1]
        memory = self._memories.get(memory_id)
        if memory is None or memory.project not in principal.projects:
            raise MemoryNotFoundError("Memory not found")
        now = self._clock()
        if memory.revision != request.expected_revision:
            raise MemoryConflictError("The memory revision changed")
        if memory.expired_at is not None or memory.superseded_by_memory_id is not None:
            raise MemoryConflictError("The memory is no longer current")
        if request.expires_at <= now:
            raise MemoryConflictError("A shared memory must expire in the future")
        updated = memory.model_copy(
            update={
                "title": request.title,
                "body": request.body,
                "provenance": request.provenance,
                "evidence": request.evidence,
                "expires_at": request.expires_at.replace(
                    microsecond=(request.expires_at.microsecond // 1_000) * 1_000
                ),
                "last_modified_by": principal.id,
                "updated_at": now,
                "revision": memory.revision + 1,
            }
        )
        self._memories[memory_id] = updated
        self._record(MemoryAuditAction.UPDATED, principal.id, updated, idempotency_key)
        response = SharedMemoryResponse(memory=updated, request_id="")
        self._idempotency[key] = (fingerprint, response)
        return response

    async def expire(
        self,
        memory_id: str,
        request: MemoryExpireRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> MemoryMutationResponse:
        key = (principal.id, f"expire:{memory_id}", idempotency_key)
        fingerprint = self._fingerprint(request)
        prior = self._idempotency.get(key)
        if prior is not None:
            if prior[0] != fingerprint:
                raise MemoryConflictError("The idempotency key was reused with different input")
            assert isinstance(prior[1], MemoryMutationResponse)
            if prior[1].memory.project not in principal.projects:
                raise MemoryNotFoundError("Memory not found")
            return prior[1]
        memory = self._memories.get(memory_id)
        if memory is None:
            raise MemoryNotFoundError("Memory not found")
        if memory.project not in principal.projects:
            raise MemoryNotFoundError("Memory not found")
        if memory.revision != request.expected_revision:
            raise MemoryConflictError("The memory revision changed")
        if memory.expired_at is not None or memory.superseded_by_memory_id is not None:
            raise MemoryConflictError("The memory is no longer current")
        now = self._clock()
        expired = memory.model_copy(
            update={
                "expired_at": now,
                "expires_at": now,
                "last_modified_by": principal.id,
                "updated_at": now,
                "revision": memory.revision + 1,
            }
        )
        self._memories[memory_id] = expired
        self._record(
            MemoryAuditAction.EXPIRED,
            principal.id,
            expired,
            idempotency_key,
            reason=request.reason,
        )
        response = MemoryMutationResponse(memory=expired, request_id="")
        self._idempotency[key] = (fingerprint, response)
        return response

    async def search(
        self, request: MemorySearchRequest, principal: Principal
    ) -> tuple[list[MemorySearchItem], str | None]:
        if request.project not in principal.projects:
            return [], None
        binding = sha256(f"{request.project}\0{request.query}".encode()).hexdigest()
        after = _decode_memory_cursor(request.cursor, "search", binding) if request.cursor else None
        query_terms = set(_tokenize(request.query))
        now = self._clock()
        scored: list[MemorySearchItem] = []
        for memory in self._memories.values():
            if (
                memory.project != request.project
                or memory.expires_at <= now
                or memory.expired_at is not None
                or memory.superseded_by_memory_id is not None
            ):
                continue
            score = sum(3 for term in _tokenize(memory.title) if term in query_terms)
            score += sum(1 for term in _tokenize(memory.body) if term in query_terms)
            if score:
                scored.append(MemorySearchItem(**memory.model_dump(), score=float(score)))
        scored.sort(key=lambda item: (-item.score, item.id))
        if after is not None:
            keys = [f"{-item.score}:{item.id}" for item in scored]
            if after not in keys:
                raise InvalidMemoryCursor("The memory cursor is stale")
            scored = scored[keys.index(after) + 1 :]
        page = scored[: request.limit + 1]
        visible = page[: request.limit]
        next_cursor = None
        if len(page) > request.limit:
            last = visible[-1]
            next_cursor = _encode_memory_cursor("search", binding, f"{-last.score}:{last.id}")
        return visible, next_cursor

    async def audit(
        self, request: MemoryAuditListRequest, principal: Principal
    ) -> tuple[list[MemoryAuditEvent], str | None]:
        memory = self._memories.get(request.memory_id)
        if (
            memory is None
            or memory.project != request.project
            or request.project not in principal.projects
        ):
            raise MemoryNotFoundError("Memory not found")
        binding = sha256(f"{request.project}\0{request.memory_id}".encode()).hexdigest()
        after = _decode_memory_cursor(request.cursor, "audit", binding) if request.cursor else None
        events = sorted(
            (event for event in self._audits if event.memory_id == request.memory_id),
            key=lambda event: (event.occurred_at, event.id),
        )
        if after is not None:
            ids = [event.id for event in events]
            if after not in ids:
                raise InvalidMemoryCursor("The memory audit cursor is stale")
            events = events[ids.index(after) + 1 :]
        page = events[: request.limit + 1]
        visible = page[: request.limit]
        next_cursor = None
        if len(page) > request.limit:
            next_cursor = _encode_memory_cursor("audit", binding, visible[-1].id)
        return visible, next_cursor


__all__ = [
    "GitSkillCatalog",
    "InMemoryKnowledgeIndex",
    "InMemorySharedMemory",
    "InvalidMemoryCursor",
    "InvalidSkillCursor",
    "KnowledgeChunk",
    "KnowledgeIndex",
    "MemoryConflictError",
    "MemoryForbiddenError",
    "MemoryNotFoundError",
    "SharedMemoryStore",
    "SkillCatalog",
    "SkillNotFoundError",
    "SkillPackage",
    "SkillRevisionNotFoundError",
    "decode_skill_cursor",
    "encode_skill_cursor",
]

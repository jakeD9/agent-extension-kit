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
    Authority,
    Citation,
    ImmutableSkillPackageManifest,
    KnowledgeResult,
    KnowledgeSearchRequest,
    MemoryAuditAction,
    MemoryAuditEvent,
    MemoryAuditListRequest,
    MemoryDecisionRequest,
    MemoryDecisionResponse,
    MemoryMutationResponse,
    MemoryProposal,
    MemoryProposalCreateRequest,
    MemoryProposalStatus,
    MemorySearchItem,
    MemorySearchRequest,
    Principal,
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
    access_groups: list[str]
    authority: Authority
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
    access_groups: list[str]
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


class GovernedMemory(Protocol):
    async def propose(
        self,
        request: MemoryProposalCreateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> MemoryProposal: ...

    async def decide(
        self,
        proposal_id: str,
        request: MemoryDecisionRequest,
        principal: Principal,
        idempotency_key: str,
        *,
        approve: bool,
    ) -> MemoryDecisionResponse: ...

    async def expire(
        self,
        memory_id: str,
        request: MemoryDecisionRequest,
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
            if not set(chunk.access_groups).intersection(principal.groups):
                continue
            if chunk.authority is not Authority.APPROVED:
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
                authority=chunk.authority,
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
        return (
            project in principal.projects
            and project in skill.projects
            and bool(set(skill.access_groups).intersection(principal.groups))
        )

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
                access_groups=skill.access_groups,
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
            access_groups=skill.access_groups,
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


class InMemoryGovernedMemory:
    """Reference governed-memory application service used by tests and mock mode."""

    def __init__(
        self,
        *,
        clock: Callable[[], datetime] | None = None,
        proposals: Sequence[MemoryProposal] = (),
        memories: Sequence[TeamMemory] = (),
        audits: Sequence[MemoryAuditEvent] = (),
        idempotency: dict[tuple[str, str, str], tuple[str, object]] | None = None,
    ) -> None:
        source_clock = clock or (lambda: datetime.now(UTC))

        def millisecond_clock() -> datetime:
            value = source_clock()
            return value.replace(microsecond=(value.microsecond // 1_000) * 1_000)

        self._clock = millisecond_clock
        self._proposals = {proposal.id: proposal for proposal in proposals}
        self._memories = {memory.id: memory for memory in memories}
        self._audits = list(audits)
        self._idempotency = dict(idempotency or {})

    def snapshot(
        self,
    ) -> tuple[
        list[MemoryProposal],
        list[TeamMemory],
        list[MemoryAuditEvent],
        dict[tuple[str, str, str], tuple[str, object]],
    ]:
        return (
            list(self._proposals.values()),
            list(self._memories.values()),
            list(self._audits),
            dict(self._idempotency),
        )

    @staticmethod
    def _fingerprint(value: BaseModel) -> str:
        return sha256(value.model_dump_json(exclude_none=True).encode()).hexdigest()

    @staticmethod
    def _can_write(
        project: str, access_groups: Sequence[str], principal: Principal, *, approve: bool = False
    ) -> bool:
        return (
            project in principal.projects
            and set(access_groups).issubset(principal.groups)
            and (not approve or "approver" in principal.roles)
        )

    def _record(
        self,
        action: MemoryAuditAction,
        actor_id: str,
        proposal: MemoryProposal,
        idempotency_key: str,
        *,
        memory_id: str | None = None,
        reason: str | None = None,
    ) -> None:
        self._audits.append(
            MemoryAuditEvent(
                id=str(uuid4()),
                action=action,
                actor_id=actor_id,
                project=proposal.project,
                proposal_id=proposal.id,
                memory_id=memory_id,
                proposal_revision=proposal.revision,
                idempotency_key=idempotency_key,
                reason=reason,
                occurred_at=self._clock(),
            )
        )

    async def propose(
        self,
        request: MemoryProposalCreateRequest,
        principal: Principal,
        idempotency_key: str,
    ) -> MemoryProposal:
        if not self._can_write(request.project, request.access_groups, principal):
            raise MemoryForbiddenError("The caller cannot propose memory for this scope")
        key = (principal.id, "propose", idempotency_key)
        fingerprint = self._fingerprint(request)
        prior = self._idempotency.get(key)
        if prior is not None:
            if prior[0] != fingerprint:
                raise MemoryConflictError("The idempotency key was reused with different input")
            assert isinstance(prior[1], MemoryProposal)
            return prior[1]
        now = self._clock()
        if request.expires_at <= now:
            raise MemoryConflictError("A proposed memory must expire in the future")
        proposal_data = request.model_dump()
        proposal_expires_at = request.expires_at
        proposal_data["expires_at"] = proposal_expires_at.replace(
            microsecond=(proposal_expires_at.microsecond // 1_000) * 1_000
        )
        proposal = MemoryProposal(
            id=str(uuid4()),
            **proposal_data,
            author_id=principal.id,
            status=MemoryProposalStatus.PROPOSED,
            created_at=now,
            updated_at=now,
            revision=1,
        )
        self._proposals[proposal.id] = proposal
        self._record(MemoryAuditAction.PROPOSED, principal.id, proposal, idempotency_key)
        self._idempotency[key] = (fingerprint, proposal)
        return proposal

    async def decide(
        self,
        proposal_id: str,
        request: MemoryDecisionRequest,
        principal: Principal,
        idempotency_key: str,
        *,
        approve: bool,
    ) -> MemoryDecisionResponse:
        action = "approve" if approve else "reject"
        key = (principal.id, f"{action}:{proposal_id}", idempotency_key)
        fingerprint = self._fingerprint(request)
        prior = self._idempotency.get(key)
        if prior is not None:
            if prior[0] != fingerprint:
                raise MemoryConflictError("The idempotency key was reused with different input")
            assert isinstance(prior[1], MemoryDecisionResponse)
            return prior[1]
        proposal = self._proposals.get(proposal_id)
        if proposal is None:
            raise MemoryNotFoundError("Memory proposal not found")
        if proposal.project not in principal.projects or not set(proposal.access_groups).issubset(
            principal.groups
        ):
            raise MemoryNotFoundError("Memory proposal not found")
        if "approver" not in principal.roles:
            raise MemoryForbiddenError("The caller cannot decide memory proposals")
        now = self._clock()
        if proposal.status is not MemoryProposalStatus.PROPOSED:
            raise MemoryConflictError("The memory proposal is no longer pending")
        if proposal.revision != request.expected_revision:
            raise MemoryConflictError("The memory proposal revision changed")
        if proposal.expires_at <= now:
            raise MemoryConflictError("The memory proposal has expired")

        memory: TeamMemory | None = None
        status = MemoryProposalStatus.APPROVED if approve else MemoryProposalStatus.REJECTED
        updated = proposal.model_copy(
            update={
                "status": status,
                "decision_reason": request.reason,
                "decision_author_id": principal.id,
                "updated_at": now,
                "revision": proposal.revision + 1,
            }
        )
        if approve:
            superseded: TeamMemory | None = None
            if proposal.supersedes_memory_id is not None:
                superseded = self._memories.get(proposal.supersedes_memory_id)
                if (
                    superseded is None
                    or superseded.project != proposal.project
                    or superseded.access_groups != proposal.access_groups
                    or superseded.superseded_by_memory_id is not None
                    or superseded.expires_at <= now
                ):
                    raise MemoryConflictError("The superseded memory is not current in this scope")
            memory_id = str(uuid4())
            memory = TeamMemory(
                id=memory_id,
                proposal_id=proposal.id,
                project=proposal.project,
                access_groups=proposal.access_groups,
                title=proposal.title,
                body=proposal.body,
                provenance=proposal.provenance,
                evidence=proposal.evidence,
                author_id=proposal.author_id,
                approved_by=principal.id,
                expires_at=proposal.expires_at,
                supersedes_memory_id=proposal.supersedes_memory_id,
                created_at=now,
                revision=updated.revision,
            )
            updated = updated.model_copy(update={"memory_id": memory_id})
            self._memories[memory_id] = memory
            if superseded is not None:
                self._memories[superseded.id] = superseded.model_copy(
                    update={"superseded_by_memory_id": memory_id}
                )
                self._record(
                    MemoryAuditAction.SUPERSEDED,
                    principal.id,
                    updated,
                    idempotency_key,
                    memory_id=superseded.id,
                    reason=request.reason,
                )
        self._proposals[proposal_id] = updated
        self._record(
            MemoryAuditAction.APPROVED if approve else MemoryAuditAction.REJECTED,
            principal.id,
            updated,
            idempotency_key,
            memory_id=memory.id if memory is not None else None,
            reason=request.reason,
        )
        response = MemoryDecisionResponse(proposal=updated, memory=memory, request_id="")
        self._idempotency[key] = (fingerprint, response)
        return response

    async def expire(
        self,
        memory_id: str,
        request: MemoryDecisionRequest,
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
            return prior[1]
        memory = self._memories.get(memory_id)
        if memory is None:
            raise MemoryNotFoundError("Memory not found")
        if memory.project not in principal.projects or not set(memory.access_groups).issubset(
            principal.groups
        ):
            raise MemoryNotFoundError("Memory not found")
        if "approver" not in principal.roles:
            raise MemoryForbiddenError("The caller cannot expire memory")
        if memory.revision != request.expected_revision:
            raise MemoryConflictError("The memory revision changed")
        if memory.expired_at is not None or memory.superseded_by_memory_id is not None:
            raise MemoryConflictError("The memory is no longer current")
        now = self._clock()
        expired = memory.model_copy(
            update={"expired_at": now, "expires_at": now, "revision": memory.revision + 1}
        )
        self._memories[memory_id] = expired
        proposal = self._proposals[memory.proposal_id]
        self._record(
            MemoryAuditAction.EXPIRED,
            principal.id,
            proposal,
            idempotency_key,
            memory_id=memory_id,
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
                or not set(memory.access_groups).intersection(principal.groups)
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
        proposal = self._proposals.get(request.proposal_id)
        if (
            proposal is None
            or proposal.project != request.project
            or request.project not in principal.projects
            or not set(proposal.access_groups).intersection(principal.groups)
        ):
            raise MemoryNotFoundError("Memory proposal not found")
        binding = sha256(f"{request.project}\0{request.proposal_id}".encode()).hexdigest()
        after = _decode_memory_cursor(request.cursor, "audit", binding) if request.cursor else None
        events = sorted(
            (event for event in self._audits if event.proposal_id == request.proposal_id),
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
    "GovernedMemory",
    "InMemoryGovernedMemory",
    "InMemoryKnowledgeIndex",
    "InvalidMemoryCursor",
    "InvalidSkillCursor",
    "KnowledgeChunk",
    "KnowledgeIndex",
    "MemoryConflictError",
    "MemoryForbiddenError",
    "MemoryNotFoundError",
    "SkillCatalog",
    "SkillNotFoundError",
    "SkillPackage",
    "SkillRevisionNotFoundError",
    "decode_skill_cursor",
    "encode_skill_cursor",
]

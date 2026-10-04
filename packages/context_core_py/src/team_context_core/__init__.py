import base64
import json
import re
import unicodedata
from collections.abc import Sequence
from typing import Protocol

from pydantic import BaseModel, ConfigDict
from team_agent_contracts import (
    Authority,
    Citation,
    ImmutableSkillPackageManifest,
    KnowledgeResult,
    KnowledgeSearchRequest,
    Principal,
    SkillDetail,
    SkillListRequest,
    SkillPackageBundle,
    SkillResolutionManifest,
    SkillResolveRequest,
    SkillSummary,
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


__all__ = [
    "GitSkillCatalog",
    "InMemoryKnowledgeIndex",
    "InvalidSkillCursor",
    "KnowledgeChunk",
    "KnowledgeIndex",
    "SkillCatalog",
    "SkillNotFoundError",
    "SkillPackage",
    "SkillRevisionNotFoundError",
    "decode_skill_cursor",
    "encode_skill_cursor",
]

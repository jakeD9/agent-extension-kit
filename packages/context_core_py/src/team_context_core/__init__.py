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
    KnowledgeResult,
    KnowledgeSearchRequest,
    Principal,
    SkillDetail,
    SkillListRequest,
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


class KnowledgeIndex(Protocol):
    async def search(
        self, request: KnowledgeSearchRequest, principal: Principal
    ) -> list[KnowledgeResult]: ...


class InvalidSkillCursor(ValueError):
    pass


class SkillCatalog(Protocol):
    async def list(
        self, request: SkillListRequest, principal: Principal
    ) -> tuple[list[SkillSummary], str | None]: ...

    async def get(self, name: str, project: str, principal: Principal) -> SkillDetail | None: ...


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


def encode_skill_cursor(project: str, after: str) -> str:
    payload = json.dumps(
        {"v": 1, "project": project, "after": after}, separators=(",", ":"), sort_keys=True
    ).encode()
    return base64.urlsafe_b64encode(payload).decode().rstrip("=")


def decode_skill_cursor(cursor: str, project: str) -> str:
    try:
        padding = "=" * (-len(cursor) % 4)
        payload = json.loads(base64.b64decode(cursor + padding, altchars=b"-_", validate=True))
        if (
            not isinstance(payload, dict)
            or set(payload) != {"v", "project", "after"}
            or payload["v"] != 1
            or payload["project"] != project
            or not isinstance(payload["after"], str)
            or not payload["after"]
        ):
            raise ValueError
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise InvalidSkillCursor("The skill cursor is invalid") from error
    return payload["after"]


class GitSkillCatalog:
    """Authorized catalog built from skill packages scanned from a pinned Git checkout."""

    def __init__(self, skills: Sequence[SkillPackage]) -> None:
        self._skills = tuple(skills)

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
            key=lambda skill: skill.id,
        )
        after = decode_skill_cursor(request.cursor, request.project) if request.cursor else None
        if after is not None:
            authorized = [skill for skill in authorized if skill.id > after]
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
            next_cursor = encode_skill_cursor(request.project, visible[-1].id)
        return items, next_cursor

    async def get(self, name: str, project: str, principal: Principal) -> SkillDetail | None:
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


__all__ = [
    "GitSkillCatalog",
    "InMemoryKnowledgeIndex",
    "InvalidSkillCursor",
    "KnowledgeChunk",
    "KnowledgeIndex",
    "SkillCatalog",
    "SkillPackage",
    "decode_skill_cursor",
    "encode_skill_cursor",
]

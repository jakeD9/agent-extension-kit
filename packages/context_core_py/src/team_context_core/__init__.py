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


class KnowledgeIndex(Protocol):
    async def search(
        self, request: KnowledgeSearchRequest, principal: Principal
    ) -> list[KnowledgeResult]: ...


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


__all__ = ["InMemoryKnowledgeIndex", "KnowledgeChunk", "KnowledgeIndex"]

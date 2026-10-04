import asyncio

import pytest
from team_agent_contracts import (
    Authority,
    Citation,
    KnowledgeSearchRequest,
    Principal,
    SkillListRequest,
)
from team_context_core import (
    InMemoryKnowledgeIndex,
    InMemorySkillCatalog,
    KnowledgeChunk,
    SkillPackage,
)


@pytest.fixture
def chunks() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            id="approved",
            project="event-ingestion",
            access_groups=["engineering"],
            authority=Authority.APPROVED,
            title="Stable idempotency keys",
            body="The idempotency key uses the vendor event identifier and never receipt time.",
            citation=Citation(
                repository="sample",
                path="knowledge/architecture/idempotency.md",
                revision="abc123",
            ),
        ),
        KnowledgeChunk(
            id="private",
            project="billing",
            access_groups=["finance"],
            authority=Authority.APPROVED,
            title="Billing secrets",
            body="Private billing material",
            citation=Citation(
                repository="sample", path="knowledge/domain/billing.md", revision="abc123"
            ),
        ),
    ]


def test_returns_stable_citation_for_authorized_match(
    chunks: list[KnowledgeChunk],
) -> None:
    index = InMemoryKnowledgeIndex(chunks)
    results = asyncio.run(
        index.search(
            KnowledgeSearchRequest(query="vendor idempotency", project="event-ingestion"),
            Principal(id="dev", groups=["engineering"], projects=["event-ingestion"]),
        )
    )

    assert len(results) == 1
    assert results[0].citation == Citation(
        repository="sample", path="knowledge/architecture/idempotency.md", revision="abc123"
    )


def test_filters_inaccessible_content_before_scoring(
    chunks: list[KnowledgeChunk],
) -> None:
    index = InMemoryKnowledgeIndex(chunks)
    results = asyncio.run(
        index.search(
            KnowledgeSearchRequest(query="billing", project="billing"),
            Principal(id="dev", groups=["engineering"], projects=["event-ingestion"]),
        )
    )

    assert results == []


def test_skill_catalog_uses_stable_cursor_pagination() -> None:
    first = SkillPackage(
        id="sample:skills/a/SKILL.md",
        name="a",
        description="A",
        version="1",
        projects=["project"],
        access_groups=["engineering"],
        allowed_tools=[],
        body="# A",
        citation=Citation(repository="sample", path="skills/a/SKILL.md", revision="rev"),
    )
    second = first.model_copy(
        update={
            "id": "sample:skills/b/SKILL.md",
            "name": "b",
            "description": "B",
            "body": "# B",
            "citation": Citation(repository="sample", path="skills/b/SKILL.md", revision="rev"),
        }
    )
    catalog = InMemorySkillCatalog([second, first])
    principal = Principal(id="dev", groups=["engineering"], projects=["project"])

    items, cursor = asyncio.run(
        catalog.list(SkillListRequest(project="project", limit=1), principal)
    )
    assert [item.name for item in items] == ["a"]
    assert cursor is not None

    items, next_cursor = asyncio.run(
        catalog.list(SkillListRequest(project="project", limit=1, cursor=cursor), principal)
    )
    assert [item.name for item in items] == ["b"]
    assert next_cursor is None

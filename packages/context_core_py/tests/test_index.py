import asyncio

import pytest
from team_agent_contracts import Authority, Citation, KnowledgeSearchRequest, Principal
from team_context_core import InMemoryKnowledgeIndex, KnowledgeChunk


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

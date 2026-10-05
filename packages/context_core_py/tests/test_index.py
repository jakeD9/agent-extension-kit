import asyncio
import hashlib
from datetime import UTC, datetime, timedelta

import pytest
from team_agent_contracts import (
    Citation,
    ImmutableSkillPackageManifest,
    KnowledgeSearchRequest,
    MemoryExpireRequest,
    Principal,
    SharedMemoryCreateRequest,
    SharedMemoryUpdateRequest,
    SkillFileManifest,
    SkillListRequest,
    SkillPackageBundle,
    SkillPackageFile,
    SkillResolveRequest,
)
from team_context_core import (
    GitSkillCatalog,
    InMemoryKnowledgeIndex,
    InMemorySharedMemory,
    InvalidSkillCursor,
    KnowledgeChunk,
    MemoryNotFoundError,
    SkillNotFoundError,
    SkillPackage,
    SkillRevisionNotFoundError,
)


def _packaged_skill(
    name: str,
    *,
    projects: list[str] | None = None,
) -> SkillPackage:
    path = f"skills/{name}/SKILL.md"
    file = SkillFileManifest(path="SKILL.md", sha256="a" * 64, size=7)
    manifest = ImmutableSkillPackageManifest(
        package_id=f"sha256:{hashlib.sha256(name.encode()).hexdigest()}",
        name=name,
        description=name,
        version="1",
        source_revision="rev",
        files=[file],
        citation=Citation(repository="sample", path=path, revision="rev"),
    )
    bundle = SkillPackageBundle(
        manifest=manifest,
        files=[
            SkillPackageFile(
                **file.model_dump(),
                content_base64="IyBTa2lsbA==",
            )
        ],
    )
    return SkillPackage(
        id=manifest.package_id,
        name=name,
        description=name,
        version="1",
        projects=projects or ["project"],
        allowed_tools=[],
        body="# Skill",
        citation=manifest.citation,
        package_manifest=manifest,
        bundle=bundle,
    )


@pytest.fixture
def chunks() -> list[KnowledgeChunk]:
    return [
        KnowledgeChunk(
            id="approved",
            project="event-ingestion",
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
    catalog = GitSkillCatalog([second, first], "rev")
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


def test_skill_catalog_rejects_cursor_from_another_revision() -> None:
    skill = _packaged_skill("review")
    catalog = GitSkillCatalog([skill, _packaged_skill("policy")], "rev")
    principal = Principal(id="dev", groups=["engineering"], projects=["project"])
    _, cursor = asyncio.run(catalog.list(SkillListRequest(project="project", limit=1), principal))
    assert cursor is not None
    later_skill = skill.model_copy(
        update={
            "citation": skill.citation.model_copy(update={"revision": "later"}),
            "package_manifest": None,
            "bundle": None,
        }
    )
    later_catalog = GitSkillCatalog([later_skill], "later")

    with pytest.raises(InvalidSkillCursor, match="cursor is invalid"):
        asyncio.run(
            later_catalog.list(
                SkillListRequest(project="project", limit=1, cursor=cursor), principal
            )
        )


def test_skill_resolution_returns_only_explicit_self_contained_packages() -> None:
    catalog = GitSkillCatalog(
        [
            _packaged_skill("review"),
            _packaged_skill("policy"),
            _packaged_skill("other", projects=["other"]),
        ],
        "rev",
    )
    principal = Principal(id="dev", groups=["engineering"], projects=["project"])

    resolved = asyncio.run(
        catalog.resolve(SkillResolveRequest(project="project", names=["review"]), principal)
    )

    assert [package.name for package in resolved.packages] == ["review"]


def test_skill_resolution_ignores_user_groups_within_project() -> None:
    catalog = GitSkillCatalog(
        [_packaged_skill("private")],
        "rev",
    )
    principal = Principal(id="dev", groups=["engineering"], projects=["project"])

    with pytest.raises(SkillRevisionNotFoundError, match="revision is unavailable"):
        asyncio.run(
            catalog.resolve(
                SkillResolveRequest(project="project", names=["private"], revision="unavailable"),
                principal,
            )
        )


def test_all_skill_resolution_checks_project_scope_before_revision_availability() -> None:
    catalog = GitSkillCatalog([_packaged_skill("review")], "rev")
    principal = Principal(id="dev", groups=["engineering"], projects=["other"])

    with pytest.raises(SkillNotFoundError, match="Skill not found"):
        asyncio.run(
            catalog.resolve(
                SkillResolveRequest(project="project", all=True, revision="unavailable"),
                principal,
            )
        )


def test_empty_skill_catalog_keeps_explicit_revision_for_all_resolution() -> None:
    catalog = GitSkillCatalog([], "empty-revision")
    principal = Principal(id="dev", groups=["engineering"], projects=["project"])

    resolved = asyncio.run(
        catalog.resolve(SkillResolveRequest(project="project", all=True), principal)
    )

    assert resolved.catalog_revision == "empty-revision"
    assert resolved.selected_names == []
    assert resolved.packages == []


def test_skill_catalog_requires_nonempty_explicit_revision() -> None:
    with pytest.raises(ValueError, match="revision is required"):
        GitSkillCatalog([], "")


def test_memory_mutations_are_available_to_every_project_member() -> None:
    memory = InMemorySharedMemory()
    author = Principal(id="author", groups=["a"], projects=["project"])
    teammate = Principal(id="teammate", groups=["different"], projects=["project"])
    request = SharedMemoryCreateRequest(
        project="project",
        title="Scoped memory",
        body="The project protects this record.",
        provenance=Citation(repository="org/repo", path="incident.md", revision="rev"),
        evidence=[Citation(repository="org/repo", path="test.py", revision="rev")],
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    created = asyncio.run(memory.create(request, author, "create-key"))
    expired = asyncio.run(
        memory.expire(
            created.memory.id,
            MemoryExpireRequest(expected_revision=1, reason="obsolete"),
            teammate,
            "expire-key",
        )
    )
    assert expired.memory.last_modified_by == "teammate"


def test_memory_receipt_replay_rechecks_current_project_membership() -> None:
    memory = InMemorySharedMemory()
    admitted = Principal(id="developer", projects=["project"])
    revoked = Principal(id="developer", projects=[])
    create_request = SharedMemoryCreateRequest(
        project="project",
        title="Scoped memory",
        body="Only current project members may replay mutations.",
        provenance=Citation(repository="org/repo", path="incident.md", revision="rev"),
        evidence=[Citation(repository="org/repo", path="test.py", revision="rev")],
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    created = asyncio.run(memory.create(create_request, admitted, "create"))
    update_request = SharedMemoryUpdateRequest(
        expected_revision=1,
        title="Updated",
        body=create_request.body,
        provenance=create_request.provenance,
        evidence=create_request.evidence,
        expires_at=create_request.expires_at,
    )
    asyncio.run(memory.update(created.memory.id, update_request, admitted, "update"))

    with pytest.raises(MemoryNotFoundError):
        asyncio.run(memory.update(created.memory.id, update_request, revoked, "update"))

    current = memory.snapshot()[0][0]
    expire_request = MemoryExpireRequest(expected_revision=current.revision, reason="obsolete")
    asyncio.run(memory.expire(current.id, expire_request, admitted, "expire"))
    with pytest.raises(MemoryNotFoundError):
        asyncio.run(memory.expire(current.id, expire_request, revoked, "expire"))

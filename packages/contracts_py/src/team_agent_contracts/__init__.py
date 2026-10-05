import re
from datetime import datetime
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

MAX_PROJECT_LENGTH = 200
MAX_SKILL_NAME_LENGTH = 128
MAX_REVISION_LENGTH = 256
SKILL_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"
MAX_MEMORY_BODY_LENGTH = 20_000
MAX_MEMORY_CURSOR_LENGTH = 4_096
MAX_CITATION_REPOSITORY_LENGTH = 500
MAX_CITATION_PATH_LENGTH = 2_000
MAX_CITATION_HEADING_LENGTH = 500


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Authority(StrEnum):
    APPROVED = "approved"
    PROPOSED = "proposed"
    SUPERSEDED = "superseded"


class MemoryProposalStatus(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"


class MemoryAuditAction(StrEnum):
    PROPOSED = "proposed"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    SUPERSEDED = "superseded"


class Principal(ContractModel):
    id: str = Field(min_length=1)
    groups: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class Citation(ContractModel):
    repository: str = Field(min_length=1, max_length=MAX_CITATION_REPOSITORY_LENGTH)
    path: str = Field(min_length=1, max_length=MAX_CITATION_PATH_LENGTH)
    revision: str = Field(min_length=1, max_length=MAX_REVISION_LENGTH)
    heading: str | None = Field(default=None, max_length=MAX_CITATION_HEADING_LENGTH)


class KnowledgeSearchRequest(ContractModel):
    query: str = Field(min_length=1, max_length=2_000)
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    limit: int = Field(default=8, ge=1, le=50)


class KnowledgeResult(ContractModel):
    id: str
    title: str
    excerpt: str
    score: float
    authority: Authority
    citation: Citation


class KnowledgeSearchResponse(ContractModel):
    results: list[KnowledgeResult]
    request_id: str


class MemoryProposalCreateRequest(ContractModel):
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    access_groups: list[Annotated[str, Field(min_length=1, max_length=128)]] = Field(
        min_length=1, max_length=50
    )
    title: str = Field(min_length=1, max_length=300)
    body: str = Field(min_length=1, max_length=MAX_MEMORY_BODY_LENGTH)
    provenance: Citation
    evidence: list[Citation] = Field(min_length=1, max_length=20)
    expires_at: AwareDatetime
    supersedes_memory_id: str | None = Field(default=None, min_length=1, max_length=128)

    @model_validator(mode="after")
    def unique_access_groups(self) -> "MemoryProposalCreateRequest":
        if len(set(self.access_groups)) != len(self.access_groups):
            raise ValueError("Memory access groups must be unique")
        return self


class MemoryProposal(ContractModel):
    schema_version: Literal["1"] = "1"
    id: str = Field(min_length=1, max_length=128)
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    access_groups: list[str]
    title: str
    body: str
    provenance: Citation
    evidence: list[Citation]
    author_id: str = Field(min_length=1, max_length=256)
    status: MemoryProposalStatus
    expires_at: AwareDatetime
    supersedes_memory_id: str | None = None
    memory_id: str | None = None
    decision_reason: str | None = None
    decision_author_id: str | None = None
    created_at: datetime
    updated_at: datetime
    revision: int = Field(ge=1)


class MemoryProposalResponse(ContractModel):
    proposal: MemoryProposal
    request_id: str


class MemoryDecisionRequest(ContractModel):
    expected_revision: int = Field(ge=1)
    reason: str = Field(min_length=1, max_length=2_000)


class TeamMemory(ContractModel):
    schema_version: Literal["1"] = "1"
    id: str = Field(min_length=1, max_length=128)
    proposal_id: str = Field(min_length=1, max_length=128)
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    access_groups: list[str]
    title: str
    body: str
    provenance: Citation
    evidence: list[Citation]
    author_id: str
    approved_by: str
    authority: Literal[Authority.APPROVED] = Authority.APPROVED
    expires_at: AwareDatetime
    supersedes_memory_id: str | None = None
    superseded_by_memory_id: str | None = None
    expired_at: AwareDatetime | None = None
    created_at: datetime
    revision: int = Field(ge=1)


class MemoryDecisionResponse(ContractModel):
    proposal: MemoryProposal
    memory: TeamMemory | None = None
    request_id: str


class MemoryMutationResponse(ContractModel):
    memory: TeamMemory
    request_id: str


class MemorySearchRequest(ContractModel):
    query: str = Field(min_length=1, max_length=2_000)
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    limit: int = Field(default=20, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_CURSOR_LENGTH)


class MemorySearchItem(TeamMemory):
    score: float = Field(gt=0)


class MemorySearchResponse(ContractModel):
    items: list[MemorySearchItem]
    next_cursor: str | None = None
    request_id: str


class MemoryAuditEvent(ContractModel):
    schema_version: Literal["1"] = "1"
    id: str = Field(min_length=1, max_length=128)
    action: MemoryAuditAction
    actor_id: str
    project: str
    proposal_id: str
    memory_id: str | None = None
    proposal_revision: int = Field(ge=1)
    idempotency_key: str = Field(min_length=1, max_length=256)
    reason: str | None = None
    occurred_at: datetime


class MemoryAuditListRequest(ContractModel):
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    proposal_id: str = Field(min_length=1, max_length=128)
    limit: int = Field(default=50, ge=1, le=100)
    cursor: str | None = Field(default=None, min_length=1, max_length=MAX_MEMORY_CURSOR_LENGTH)


class MemoryAuditResponse(ContractModel):
    items: list[MemoryAuditEvent]
    next_cursor: str | None = None
    request_id: str


class SkillListRequest(ContractModel):
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    limit: int = Field(default=50, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=4_096)


class SkillGetRequest(ContractModel):
    name: str = Field(min_length=1, max_length=MAX_SKILL_NAME_LENGTH, pattern=SKILL_NAME_PATTERN)
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    revision: str | None = Field(default=None, min_length=1, max_length=MAX_REVISION_LENGTH)


class SkillPackageGetRequest(ContractModel):
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)


class SkillSummary(ContractModel):
    name: str = Field(min_length=1, max_length=MAX_SKILL_NAME_LENGTH, pattern=SKILL_NAME_PATTERN)
    description: str
    version: str
    projects: list[str]
    access_groups: list[str]
    allowed_tools: list[str]
    citation: Citation


class SkillDetail(SkillSummary):
    body: str


class SkillListResponse(ContractModel):
    items: list[SkillSummary]
    next_cursor: str | None = None
    request_id: str


class SkillGetResponse(SkillDetail):
    request_id: str


class SkillFileManifest(ContractModel):
    path: str = Field(min_length=1)
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    size: int = Field(ge=0)


class ImmutableSkillPackageManifest(ContractModel):
    schema_version: Literal["1"] = "1"
    package_id: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    name: str = Field(min_length=1, max_length=MAX_SKILL_NAME_LENGTH, pattern=SKILL_NAME_PATTERN)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_revision: str = Field(min_length=1, max_length=MAX_REVISION_LENGTH)
    files: list[SkillFileManifest] = Field(min_length=1)
    resources: list[str] = Field(default_factory=list)
    citation: Citation


class SkillPackageFile(SkillFileManifest):
    content_base64: str


class SkillPackageBundle(ContractModel):
    manifest: ImmutableSkillPackageManifest
    files: list[SkillPackageFile] = Field(min_length=1)


class SkillResolveRequest(ContractModel):
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    names: (
        list[
            Annotated[
                str,
                Field(
                    min_length=1,
                    max_length=MAX_SKILL_NAME_LENGTH,
                    pattern=SKILL_NAME_PATTERN,
                ),
            ]
        ]
        | None
    ) = Field(default=None, min_length=1, max_length=100)
    all: bool = False
    revision: str | None = Field(default=None, min_length=1, max_length=MAX_REVISION_LENGTH)

    @model_validator(mode="after")
    def exactly_one_selection_mode(self) -> "SkillResolveRequest":
        if (self.names is not None) == self.all:
            raise ValueError("Exactly one of names or all is required")
        if self.names is not None and len(set(self.names)) != len(self.names):
            raise ValueError("Skill names must be unique")
        return self


class SkillResolutionManifest(ContractModel):
    schema_version: Literal["1"] = "1"
    catalog_revision: str = Field(min_length=1, max_length=MAX_REVISION_LENGTH)
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    selected_names: list[str]
    packages: list[ImmutableSkillPackageManifest]


class SkillResolveResponse(ContractModel):
    manifest: SkillResolutionManifest
    request_id: str


class SkillPackageResponse(SkillPackageBundle):
    request_id: str


class SkillLockPackage(ImmutableSkillPackageManifest):
    """Credential-free immutable package identity stored in a pull lock."""


class SkillLock(ContractModel):
    schema_version: Literal["1"] = "1"
    catalog_revision: str = Field(min_length=1, max_length=MAX_REVISION_LENGTH)
    project: str = Field(min_length=1, max_length=MAX_PROJECT_LENGTH)
    target: Literal["generic", "codex", "claude"] = "generic"
    packages: list[SkillLockPackage] = Field(max_length=100)

    @model_validator(mode="after")
    def unique_packages(self) -> "SkillLock":
        names = [package.name for package in self.packages]
        package_ids = [package.package_id for package in self.packages]
        if len(set(names)) != len(names) or len(set(package_ids)) != len(package_ids):
            raise ValueError("Locked skill names and package IDs must be unique")
        if any(not re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", name) for name in names):
            raise ValueError("Locked skill names must use kebab-case")
        if any(package.source_revision != self.catalog_revision for package in self.packages):
            raise ValueError("Locked packages must match the catalog revision")
        return self


class ErrorDetail(ContractModel):
    code: str
    message: str
    request_id: str
    details: dict[str, object] | None = None


class ErrorResponse(ContractModel):
    error: ErrorDetail


__all__ = [
    "MAX_CITATION_HEADING_LENGTH",
    "MAX_CITATION_PATH_LENGTH",
    "MAX_CITATION_REPOSITORY_LENGTH",
    "MAX_MEMORY_BODY_LENGTH",
    "MAX_MEMORY_CURSOR_LENGTH",
    "MAX_PROJECT_LENGTH",
    "MAX_REVISION_LENGTH",
    "MAX_SKILL_NAME_LENGTH",
    "SKILL_NAME_PATTERN",
    "Authority",
    "Citation",
    "ErrorDetail",
    "ErrorResponse",
    "ImmutableSkillPackageManifest",
    "KnowledgeResult",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResponse",
    "MemoryAuditAction",
    "MemoryAuditEvent",
    "MemoryAuditListRequest",
    "MemoryAuditResponse",
    "MemoryDecisionRequest",
    "MemoryDecisionResponse",
    "MemoryMutationResponse",
    "MemoryProposal",
    "MemoryProposalCreateRequest",
    "MemoryProposalResponse",
    "MemoryProposalStatus",
    "MemorySearchItem",
    "MemorySearchRequest",
    "MemorySearchResponse",
    "Principal",
    "SkillDetail",
    "SkillFileManifest",
    "SkillGetRequest",
    "SkillGetResponse",
    "SkillListRequest",
    "SkillListResponse",
    "SkillLock",
    "SkillLockPackage",
    "SkillPackageBundle",
    "SkillPackageFile",
    "SkillPackageGetRequest",
    "SkillPackageResponse",
    "SkillResolutionManifest",
    "SkillResolveRequest",
    "SkillResolveResponse",
    "SkillSummary",
    "TeamMemory",
]

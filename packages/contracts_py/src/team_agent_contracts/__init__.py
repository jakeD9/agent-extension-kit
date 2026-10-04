import re
from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

MAX_PROJECT_LENGTH = 200
MAX_SKILL_NAME_LENGTH = 128
MAX_REVISION_LENGTH = 256
SKILL_NAME_PATTERN = r"^[a-z0-9]+(?:-[a-z0-9]+)*$"


class ContractModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Authority(StrEnum):
    APPROVED = "approved"
    PROPOSED = "proposed"
    SUPERSEDED = "superseded"


class Principal(ContractModel):
    id: str = Field(min_length=1)
    groups: list[str] = Field(default_factory=list)
    projects: list[str] = Field(default_factory=list)
    roles: list[str] = Field(default_factory=list)


class Citation(ContractModel):
    repository: str
    path: str
    revision: str = Field(min_length=1, max_length=MAX_REVISION_LENGTH)
    heading: str | None = None


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
]

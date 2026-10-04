from enum import StrEnum
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


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
    revision: str
    heading: str | None = None


class KnowledgeSearchRequest(ContractModel):
    query: str = Field(min_length=1, max_length=2_000)
    project: str = Field(min_length=1)
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
    project: str = Field(min_length=1)
    limit: int = Field(default=50, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=4_096)


class SkillGetRequest(ContractModel):
    project: str = Field(min_length=1)
    revision: str | None = Field(default=None, min_length=1)


class SkillPackageGetRequest(ContractModel):
    project: str = Field(min_length=1)


class SkillSummary(ContractModel):
    name: str
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
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    version: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    files: list[SkillFileManifest] = Field(min_length=1)
    resources: list[str] = Field(default_factory=list)
    citation: Citation


class SkillPackageFile(SkillFileManifest):
    content_base64: str


class SkillPackageBundle(ContractModel):
    manifest: ImmutableSkillPackageManifest
    files: list[SkillPackageFile] = Field(min_length=1)


class SkillResolveRequest(ContractModel):
    project: str = Field(min_length=1)
    names: list[Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$")]] | None = Field(
        default=None, min_length=1, max_length=100
    )
    all: bool = False
    revision: str | None = Field(default=None, min_length=1)

    @model_validator(mode="after")
    def exactly_one_selection_mode(self) -> "SkillResolveRequest":
        if (self.names is not None) == self.all:
            raise ValueError("Exactly one of names or all is required")
        if self.names is not None and len(set(self.names)) != len(self.names):
            raise ValueError("Skill names must be unique")
        return self


class SkillResolutionManifest(ContractModel):
    schema_version: Literal["1"] = "1"
    catalog_revision: str = Field(min_length=1)
    project: str
    selected_names: list[str]
    packages: list[ImmutableSkillPackageManifest]


class SkillResolveResponse(ContractModel):
    manifest: SkillResolutionManifest
    request_id: str


class SkillPackageResponse(SkillPackageBundle):
    request_id: str


class ErrorDetail(ContractModel):
    code: str
    message: str
    request_id: str
    details: dict[str, object] | None = None


class ErrorResponse(ContractModel):
    error: ErrorDetail


__all__ = [
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
    "SkillPackageBundle",
    "SkillPackageFile",
    "SkillPackageGetRequest",
    "SkillPackageResponse",
    "SkillResolutionManifest",
    "SkillResolveRequest",
    "SkillResolveResponse",
    "SkillSummary",
]

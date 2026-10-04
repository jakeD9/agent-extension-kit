from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


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
    request_id: str = Field(serialization_alias="requestId")


class SkillListRequest(ContractModel):
    project: str = Field(min_length=1)
    limit: int = Field(default=50, ge=1, le=50)
    cursor: str | None = Field(default=None, min_length=1, max_length=4_096)


class SkillGetRequest(ContractModel):
    project: str = Field(min_length=1)


class SkillSummary(ContractModel):
    name: str
    description: str
    version: str
    projects: list[str]
    access_groups: list[str] = Field(serialization_alias="accessGroups")
    allowed_tools: list[str] = Field(serialization_alias="allowedTools")
    citation: Citation


class SkillDetail(SkillSummary):
    body: str


class SkillListResponse(ContractModel):
    items: list[SkillSummary]
    next_cursor: str | None = Field(default=None, serialization_alias="nextCursor")
    request_id: str = Field(serialization_alias="requestId")


class SkillGetResponse(SkillDetail):
    request_id: str = Field(serialization_alias="requestId")


class ErrorDetail(ContractModel):
    code: str
    message: str
    request_id: str = Field(serialization_alias="requestId")
    details: dict[str, object] | None = None


class ErrorResponse(ContractModel):
    error: ErrorDetail


__all__ = [
    "Authority",
    "Citation",
    "ErrorDetail",
    "ErrorResponse",
    "KnowledgeResult",
    "KnowledgeSearchRequest",
    "KnowledgeSearchResponse",
    "Principal",
    "SkillDetail",
    "SkillGetRequest",
    "SkillGetResponse",
    "SkillListRequest",
    "SkillListResponse",
    "SkillSummary",
]

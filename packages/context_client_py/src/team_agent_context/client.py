import json
import re
from collections.abc import Mapping
from types import TracebackType
from typing import Self, TypeVar

import httpx
from pydantic import BaseModel, ValidationError
from team_agent_contracts import (
    MAX_SKILL_NAME_LENGTH,
    SKILL_NAME_PATTERN,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    SkillGetRequest,
    SkillGetResponse,
    SkillListRequest,
    SkillListResponse,
)

MAX_RESPONSE_BYTES = 16 * 1024 * 1024
ResponseModel = TypeVar("ResponseModel", bound=BaseModel)


class ContextClientError(RuntimeError):
    """A bounded, credential-free error suitable for CLI and MCP callers."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


_ERROR_MESSAGES = {
    "unauthorized": "Authentication failed; check TEAM_AGENT_TOKEN.",
    "forbidden": "The caller is not authorized for this project or operation.",
    "skill_not_found": "The skill was not found or is outside the caller's authorized scope.",
    "skill_revision_not_found": "The requested skill revision is unavailable.",
    "validation_failed": "The context request is invalid; check its project, cursor, and limits.",
    "internal_error": (
        "The context service could not complete the request; retry or contact an administrator."
    ),
    "service_unavailable": "The context service is unavailable; retry later.",
}
_SKILL_NAME = re.compile(SKILL_NAME_PATTERN)


class ContextClient:
    """Authenticated async client for model-facing context read operations."""

    def __init__(
        self,
        base_url: str,
        token: str,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        if not token:
            raise ContextClientError(
                "configuration_error",
                "TEAM_AGENT_CONTEXT_URL and TEAM_AGENT_TOKEN are required.",
            )
        try:
            url = httpx.URL(base_url)
        except httpx.InvalidURL as error:
            raise ContextClientError(
                "configuration_error",
                "TEAM_AGENT_CONTEXT_URL must be a root HTTP or HTTPS service URL.",
            ) from error
        if (
            url.scheme not in {"http", "https"}
            or not url.host
            or url.username
            or url.password
            or url.query
            or url.fragment
            or url.path not in {"", "/"}
        ):
            raise ContextClientError(
                "configuration_error",
                "TEAM_AGENT_CONTEXT_URL must be a root HTTP or HTTPS service URL.",
            )
        self._client = httpx.AsyncClient(
            base_url=str(url).rstrip("/"),
            headers={"authorization": f"Bearer {token}"},
            transport=transport,
            timeout=timeout,
        )

    async def __aenter__(self) -> Self:
        return self

    async def __aexit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        await self.close()

    async def close(self) -> None:
        await self._client.aclose()

    async def _request(
        self,
        model: type[ResponseModel],
        method: str,
        path: str,
        *,
        params: Mapping[str, str] | None = None,
        json_body: object | None = None,
    ) -> ResponseModel:
        payload = bytearray()
        try:
            async with self._client.stream(method, path, params=params, json=json_body) as response:
                async for chunk in response.aiter_bytes():
                    if len(payload) + len(chunk) > MAX_RESPONSE_BYTES:
                        raise ContextClientError(
                            "response_too_large",
                            "The context service response exceeded the safe size limit.",
                        )
                    payload.extend(chunk)
                if response.is_error:
                    code = f"http_{response.status_code}"
                    try:
                        body = json.loads(payload)
                        candidate = body.get("error", {}).get("code")
                        if isinstance(candidate, str) and candidate in _ERROR_MESSAGES:
                            code = candidate
                    except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                        pass
                    if code not in _ERROR_MESSAGES:
                        code = "service_error"
                    message = _ERROR_MESSAGES.get(code, "The context service rejected the request.")
                    raise ContextClientError(code, message)
        except ContextClientError:
            raise
        except httpx.HTTPError as error:
            raise ContextClientError(
                "service_unavailable",
                "The context service is unavailable; verify TEAM_AGENT_CONTEXT_URL and retry.",
            ) from error
        try:
            return model.model_validate_json(payload)
        except ValidationError as error:
            raise ContextClientError(
                "invalid_response", "The context service returned an invalid response."
            ) from error

    async def search(self, query: str, project: str, *, limit: int = 8) -> KnowledgeSearchResponse:
        request = KnowledgeSearchRequest(query=query, project=project, limit=limit)
        return await self._request(
            KnowledgeSearchResponse,
            "POST",
            "/v1/knowledge/search",
            json_body=request.model_dump(mode="json"),
        )

    async def list_skills(
        self, project: str, *, limit: int = 50, cursor: str | None = None
    ) -> SkillListResponse:
        request = SkillListRequest(project=project, limit=limit, cursor=cursor)
        params = {key: str(value) for key, value in request.model_dump(exclude_none=True).items()}
        return await self._request(SkillListResponse, "GET", "/v1/skills", params=params)

    async def get_skill(
        self, name: str, project: str, *, revision: str | None = None
    ) -> SkillGetResponse:
        if len(name) > MAX_SKILL_NAME_LENGTH or _SKILL_NAME.fullmatch(name) is None:
            raise ContextClientError("invalid_request", "Skill names must use nonempty kebab-case.")
        request = SkillGetRequest(name=name, project=project, revision=revision)
        params = {
            key: str(value)
            for key, value in request.model_dump(exclude={"name"}, exclude_none=True).items()
        }
        return await self._request(
            SkillGetResponse, "GET", f"/v1/skills/{request.name}", params=params
        )


__all__ = ["ContextClient", "ContextClientError"]

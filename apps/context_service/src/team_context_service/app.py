import re
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated
from uuid import uuid4

from fastapi import Body, FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.middleware.base import RequestResponseEndpoint
from starlette.responses import Response
from team_agent_auth import Authenticator
from team_agent_contracts import (
    ErrorDetail,
    ErrorResponse,
    KnowledgeSearchRequest,
    KnowledgeSearchResponse,
    SkillGetRequest,
    SkillGetResponse,
    SkillListRequest,
    SkillListResponse,
    SkillPackageGetRequest,
    SkillPackageResponse,
    SkillResolveRequest,
    SkillResolveResponse,
)
from team_context_core import (
    GitSkillCatalog,
    InMemoryKnowledgeIndex,
    InvalidSkillCursor,
    KnowledgeIndex,
    SkillCatalog,
    SkillNotFoundError,
    SkillRevisionNotFoundError,
)

from team_context_service.content_pack import ContentPack

Readiness = Callable[[], Awaitable[tuple[str, dict[str, object] | None]]]
LifecycleHook = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class AppDependencies:
    pack: ContentPack
    authenticator: Authenticator
    knowledge_index: KnowledgeIndex | None = None
    skill_catalog: SkillCatalog | None = None
    readiness: Readiness | None = None
    startup: LifecycleHook | None = None
    shutdown: LifecycleHook | None = None


def _error(code: str, message: str, request_id: str, status_code: int) -> JSONResponse:
    response = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=request_id))
    return JSONResponse(status_code=status_code, content=response.model_dump(exclude_none=True))


def build_app(dependencies: AppDependencies) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        if dependencies.startup is not None:
            await dependencies.startup()
        try:
            yield
        finally:
            if dependencies.shutdown is not None:
                await dependencies.shutdown()

    app = FastAPI(title="Team Context Service", version="0.2.0", lifespan=lifespan)
    index = dependencies.knowledge_index or InMemoryKnowledgeIndex(dependencies.pack.chunks)
    skills = dependencies.skill_catalog or GitSkillCatalog(
        dependencies.pack.skills, dependencies.pack.revision
    )

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or str(uuid4())
        response = await call_next(request)
        response.headers["x-request-id"] = request.state.request_id
        return response

    @app.exception_handler(RequestValidationError)
    async def validation_error(
        request: Request, _error_value: RequestValidationError
    ) -> JSONResponse:
        return _error("validation_failed", "The request is invalid", request.state.request_id, 422)

    @app.exception_handler(Exception)
    async def internal_error(request: Request, _error_value: Exception) -> JSONResponse:
        return _error(
            "internal_error",
            "The request could not be completed",
            request.state.request_id,
            500,
        )

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "healthy"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        status = "ready"
        details: dict[str, object] | None = None
        if dependencies.readiness is not None:
            status, details = await dependencies.readiness()
        body: dict[str, object] = {
            "status": status,
            "content_pack": dependencies.pack.manifest.id,
            "chunks": len(dependencies.pack.chunks),
        }
        if details is not None:
            body["details"] = details
        return JSONResponse(status_code=200 if status == "ready" else 503, content=body)

    @app.post("/v1/knowledge/search")
    async def search(
        body: Annotated[object, Body()],
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        principal = await dependencies.authenticator.authenticate(authorization)
        if principal is None:
            return _error(
                "unauthorized",
                "A valid bearer token is required",
                request.state.request_id,
                401,
            )
        try:
            search_request = KnowledgeSearchRequest.model_validate(body)
        except ValidationError:
            return _error(
                "validation_failed",
                "The search request is invalid",
                request.state.request_id,
                422,
            )
        results = await index.search(search_request, principal)
        response = KnowledgeSearchResponse(results=results, request_id=request.state.request_id)
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))

    @app.get("/v1/skills")
    async def list_skills(
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        principal = await dependencies.authenticator.authenticate(authorization)
        if principal is None:
            return _error(
                "unauthorized",
                "A valid bearer token is required",
                request.state.request_id,
                401,
            )
        try:
            list_request = SkillListRequest.model_validate(dict(request.query_params))
            items, next_cursor = await skills.list(list_request, principal)
        except (ValidationError, InvalidSkillCursor):
            return _error(
                "validation_failed",
                "The skill list request is invalid",
                request.state.request_id,
                422,
            )
        response = SkillListResponse(
            items=items, next_cursor=next_cursor, request_id=request.state.request_id
        )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))

    @app.post("/v1/skills:resolve")
    async def resolve_skills(
        body: Annotated[object, Body()],
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        principal = await dependencies.authenticator.authenticate(authorization)
        if principal is None:
            return _error(
                "unauthorized",
                "A valid bearer token is required",
                request.state.request_id,
                401,
            )
        try:
            resolve_request = SkillResolveRequest.model_validate(body)
        except ValidationError:
            return _error(
                "validation_failed",
                "The skill resolution request is invalid",
                request.state.request_id,
                422,
            )
        try:
            manifest = await skills.resolve(resolve_request, principal)
        except SkillRevisionNotFoundError:
            return _error(
                "skill_revision_not_found",
                "Skill revision not found",
                request.state.request_id,
                404,
            )
        except SkillNotFoundError:
            return _error("skill_not_found", "Skill not found", request.state.request_id, 404)
        response = SkillResolveResponse(manifest=manifest, request_id=request.state.request_id)
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))

    @app.get("/v1/skills/{name}")
    async def get_skill(
        name: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        principal = await dependencies.authenticator.authenticate(authorization)
        if principal is None:
            return _error(
                "unauthorized",
                "A valid bearer token is required",
                request.state.request_id,
                401,
            )
        try:
            get_request = SkillGetRequest.model_validate(
                {**dict(request.query_params), "name": name}
            )
        except ValidationError:
            return _error(
                "validation_failed",
                "The skill request is invalid",
                request.state.request_id,
                422,
            )
        try:
            skill = await skills.get(
                get_request.name, get_request.project, principal, get_request.revision
            )
        except SkillRevisionNotFoundError:
            return _error(
                "skill_revision_not_found",
                "Skill revision not found",
                request.state.request_id,
                404,
            )
        if skill is None:
            return _error("skill_not_found", "Skill not found", request.state.request_id, 404)
        response = SkillGetResponse(**skill.model_dump(), request_id=request.state.request_id)
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))

    @app.get("/v1/skill-packages/{package_id}")
    async def get_skill_package(
        package_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        principal = await dependencies.authenticator.authenticate(authorization)
        if principal is None:
            return _error(
                "unauthorized",
                "A valid bearer token is required",
                request.state.request_id,
                401,
            )
        try:
            get_request = SkillPackageGetRequest.model_validate(dict(request.query_params))
        except ValidationError:
            return _error(
                "validation_failed",
                "The skill package request is invalid",
                request.state.request_id,
                422,
            )
        if re.fullmatch(r"sha256:[0-9a-f]{64}", package_id) is None:
            return _error(
                "validation_failed",
                "The skill package request is invalid",
                request.state.request_id,
                422,
            )
        bundle = await skills.get_package(package_id, get_request.project, principal)
        if bundle is None:
            return _error(
                "skill_package_not_found",
                "Skill package not found",
                request.state.request_id,
                404,
            )
        response = SkillPackageResponse(**bundle.model_dump(), request_id=request.state.request_id)
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))

    return app


__all__ = ["AppDependencies", "build_app"]

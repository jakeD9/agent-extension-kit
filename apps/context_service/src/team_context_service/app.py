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
    MemoryAuditListRequest,
    MemoryAuditResponse,
    MemoryExpireRequest,
    MemoryMutationResponse,
    MemorySearchRequest,
    MemorySearchResponse,
    Principal,
    SharedMemoryCreateRequest,
    SharedMemoryResponse,
    SharedMemoryUpdateRequest,
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
    InMemorySharedMemory,
    InvalidMemoryCursor,
    InvalidSkillCursor,
    KnowledgeIndex,
    MemoryConflictError,
    MemoryForbiddenError,
    MemoryNotFoundError,
    SharedMemoryStore,
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
    shared_memory: SharedMemoryStore | None = None
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
    memories = dependencies.shared_memory or InMemorySharedMemory()

    @app.middleware("http")
    async def request_id_middleware(
        request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        request.state.request_id = request.headers.get("x-request-id") or str(uuid4())
        if request.url.path.startswith("/v1/") and dependencies.readiness is not None:
            status, _details = await dependencies.readiness()
            if status != "ready":
                unavailable_response = _error(
                    "service_unavailable",
                    "The context service is not ready",
                    request.state.request_id,
                    503,
                )
                unavailable_response.headers["x-request-id"] = request.state.request_id
                return unavailable_response
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

    async def authenticated(
        authorization: str | None, request: Request
    ) -> tuple[Principal | None, JSONResponse | None]:
        principal = await dependencies.authenticator.authenticate(authorization)
        if principal is None:
            return None, _error(
                "unauthorized",
                "A valid bearer token is required",
                request.state.request_id,
                401,
            )
        return principal, None

    def validate_idempotency(value: str | None, request: Request) -> JSONResponse | None:
        if value is None or not (1 <= len(value) <= 256):
            return _error(
                "validation_failed",
                "A bounded Idempotency-Key header is required",
                request.state.request_id,
                422,
            )
        return None

    @app.post(
        "/v1/memories",
        status_code=201,
        response_model=SharedMemoryResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": SharedMemoryCreateRequest.model_json_schema()}
                },
            }
        },
    )
    async def create_memory(
        body: Annotated[object, Body()],
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        principal, auth_error = await authenticated(authorization, request)
        if auth_error is not None:
            return auth_error
        if (header_error := validate_idempotency(idempotency_key, request)) is not None:
            return header_error
        try:
            create_request = SharedMemoryCreateRequest.model_validate(body)
        except ValidationError:
            return _error(
                "validation_failed", "The shared memory is invalid", request.state.request_id, 422
            )
        try:
            assert principal is not None and idempotency_key is not None
            result = await memories.create(create_request, principal, idempotency_key)
        except MemoryForbiddenError:
            return _error(
                "forbidden",
                "The caller cannot create memory for this project",
                request.state.request_id,
                403,
            )
        except MemoryConflictError:
            return _error(
                "memory_conflict", "The memory operation conflicts", request.state.request_id, 409
            )
        response = SharedMemoryResponse(memory=result.memory, request_id=request.state.request_id)
        return JSONResponse(
            status_code=201,
            content=response.model_dump(mode="json", exclude_none=True),
            headers={"location": f"/v1/memories/{result.memory.id}"},
        )

    @app.put(
        "/v1/memories/{memory_id}",
        response_model=SharedMemoryResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": SharedMemoryUpdateRequest.model_json_schema()}
                },
            }
        },
    )
    async def update_memory(
        memory_id: str,
        body: Annotated[object, Body()],
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        principal, auth_error = await authenticated(authorization, request)
        if auth_error is not None:
            return auth_error
        if (header_error := validate_idempotency(idempotency_key, request)) is not None:
            return header_error
        if not memory_id or len(memory_id) > 128:
            return _error(
                "validation_failed",
                "The memory id is invalid",
                request.state.request_id,
                422,
            )
        try:
            update_request = SharedMemoryUpdateRequest.model_validate(body)
        except ValidationError:
            return _error(
                "validation_failed", "The memory update is invalid", request.state.request_id, 422
            )
        try:
            assert principal is not None and idempotency_key is not None
            result = await memories.update(memory_id, update_request, principal, idempotency_key)
        except MemoryNotFoundError:
            return _error(
                "memory_not_found",
                "Memory not found",
                request.state.request_id,
                404,
            )
        except MemoryConflictError:
            return _error(
                "memory_conflict", "The memory operation conflicts", request.state.request_id, 409
            )
        return JSONResponse(
            content=result.model_copy(update={"request_id": request.state.request_id}).model_dump(
                mode="json", exclude_none=True
            )
        )

    @app.post(
        "/v1/memories/search",
        response_model=MemorySearchResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": MemorySearchRequest.model_json_schema()}
                },
            }
        },
    )
    async def search_memories(
        body: Annotated[object, Body()],
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        principal, auth_error = await authenticated(authorization, request)
        if auth_error is not None:
            return auth_error
        try:
            search_request = MemorySearchRequest.model_validate(body)
            assert principal is not None
            items, next_cursor = await memories.search(search_request, principal)
        except (ValidationError, InvalidMemoryCursor):
            return _error(
                "validation_failed", "The memory search is invalid", request.state.request_id, 422
            )
        response = MemorySearchResponse(
            items=items, next_cursor=next_cursor, request_id=request.state.request_id
        )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))

    @app.post(
        "/v1/memories/{memory_id}/expire",
        response_model=MemoryMutationResponse,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {"schema": MemoryExpireRequest.model_json_schema()}
                },
            }
        },
    )
    async def expire_memory(
        memory_id: str,
        body: Annotated[object, Body()],
        request: Request,
        authorization: str | None = Header(default=None),
        idempotency_key: str | None = Header(default=None),
    ) -> JSONResponse:
        principal, auth_error = await authenticated(authorization, request)
        if auth_error is not None:
            return auth_error
        if (header_error := validate_idempotency(idempotency_key, request)) is not None:
            return header_error
        if not memory_id or len(memory_id) > 128:
            return _error(
                "validation_failed", "The memory id is invalid", request.state.request_id, 422
            )
        try:
            decision_request = MemoryExpireRequest.model_validate(body)
        except ValidationError:
            return _error(
                "validation_failed",
                "The memory expiration is invalid",
                request.state.request_id,
                422,
            )
        try:
            assert principal is not None and idempotency_key is not None
            result = await memories.expire(
                memory_id,
                decision_request,
                principal,
                idempotency_key,
            )
        except MemoryNotFoundError:
            return _error("memory_not_found", "Memory not found", request.state.request_id, 404)
        except MemoryConflictError:
            return _error(
                "memory_conflict", "The memory operation conflicts", request.state.request_id, 409
            )
        response = MemoryMutationResponse(memory=result.memory, request_id=request.state.request_id)
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))

    @app.get(
        "/v1/memories/{memory_id}/audit",
        response_model=MemoryAuditResponse,
    )
    async def list_memory_audit(
        memory_id: str,
        request: Request,
        authorization: str | None = Header(default=None),
    ) -> JSONResponse:
        principal, auth_error = await authenticated(authorization, request)
        if auth_error is not None:
            return auth_error
        try:
            audit_request = MemoryAuditListRequest.model_validate(
                {**dict(request.query_params), "memory_id": memory_id}
            )
            assert principal is not None
            items, next_cursor = await memories.audit(audit_request, principal)
        except (ValidationError, InvalidMemoryCursor):
            return _error(
                "validation_failed",
                "The memory audit request is invalid",
                request.state.request_id,
                422,
            )
        except MemoryNotFoundError:
            return _error(
                "memory_not_found",
                "Memory not found",
                request.state.request_id,
                404,
            )
        response = MemoryAuditResponse(
            items=items, next_cursor=next_cursor, request_id=request.state.request_id
        )
        return JSONResponse(content=response.model_dump(mode="json", exclude_none=True))

    return app


__all__ = ["AppDependencies", "build_app"]

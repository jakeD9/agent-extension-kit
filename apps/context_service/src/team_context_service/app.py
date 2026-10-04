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
)
from team_context_core import InMemoryKnowledgeIndex, KnowledgeIndex

from team_context_service.content_pack import ContentPack

Readiness = Callable[[], Awaitable[tuple[str, dict[str, object] | None]]]
LifecycleHook = Callable[[], Awaitable[None]]


@dataclass(frozen=True)
class AppDependencies:
    pack: ContentPack
    authenticator: Authenticator
    knowledge_index: KnowledgeIndex | None = None
    readiness: Readiness | None = None
    startup: LifecycleHook | None = None
    shutdown: LifecycleHook | None = None


def _error(code: str, message: str, request_id: str, status_code: int) -> JSONResponse:
    response = ErrorResponse(error=ErrorDetail(code=code, message=message, request_id=request_id))
    return JSONResponse(
        status_code=status_code, content=response.model_dump(by_alias=True, exclude_none=True)
    )


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
        return _error(
            "validation_failed", "The search request is invalid", request.state.request_id, 422
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
            "contentPack": dependencies.pack.manifest.id,
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
        return JSONResponse(content=response.model_dump(by_alias=True, mode="json"))

    return app


__all__ = ["AppDependencies", "build_app"]

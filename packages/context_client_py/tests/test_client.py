import asyncio

import httpx
import pytest
from team_agent_context.client import ContextClient, ContextClientError
from team_agent_contracts import KnowledgeSearchResponse


def test_search_returns_the_snake_case_rest_contract() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer secret"
        assert request.url.path == "/v1/knowledge/search"
        assert request.read().decode() == (
            '{"query":"stable identifier","project":"ingestion","limit":3}'
        )
        return httpx.Response(
            200,
            json={
                "results": [
                    {
                        "id": "doc-1#0",
                        "title": "Stable identity",
                        "excerpt": "Use the vendor event ID.",
                        "score": 4.0,
                        "authority": "approved",
                        "citation": {
                            "repository": "org/context",
                            "path": "knowledge/idempotency.md",
                            "revision": "rev-1",
                            "heading": "Identity",
                        },
                    }
                ],
                "request_id": "request-1",
            },
        )

    async def exercise() -> KnowledgeSearchResponse:
        async with ContextClient(
            "https://context.example",
            "secret",
            transport=httpx.MockTransport(handler),
        ) as client:
            return await client.search("stable identifier", "ingestion", limit=3)

    response = asyncio.run(exercise())

    assert response.model_dump(mode="json")["request_id"] == "request-1"
    assert response.results[0].citation.model_dump(mode="json") == {
        "repository": "org/context",
        "path": "knowledge/idempotency.md",
        "revision": "rev-1",
        "heading": "Identity",
    }


def test_client_translates_service_errors_without_disclosing_credentials() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            403,
            json={
                "error": {
                    "code": "forbidden",
                    "message": "internal policy detail",
                    "request_id": "request-2",
                }
            },
        )

    async def exercise() -> None:
        async with ContextClient(
            "https://context.example",
            "super-secret",
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.search("anything", "restricted")

    with pytest.raises(ContextClientError, match="not authorized") as captured:
        asyncio.run(exercise())

    assert captured.value.code == "forbidden"
    assert "super-secret" not in str(captured.value)
    assert "internal policy detail" not in str(captured.value)


def test_client_rejects_unsafe_skill_name_before_http_request() -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(500)

    async def exercise() -> None:
        async with ContextClient(
            "https://context.example",
            "secret",
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.get_skill("../../secrets", "platform")

    with pytest.raises(ContextClientError, match="kebab-case"):
        asyncio.run(exercise())
    assert called is False


def test_client_collapses_untrusted_upstream_error_codes() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            json={"error": {"code": "database_password_is_hunter2"}},
        )

    async def exercise() -> None:
        async with ContextClient(
            "https://context.example",
            "secret",
            transport=httpx.MockTransport(handler),
        ) as client:
            await client.search("anything", "platform")

    with pytest.raises(ContextClientError) as captured:
        asyncio.run(exercise())
    assert captured.value.code == "service_error"
    assert "hunter2" not in str(captured.value)


@pytest.mark.parametrize(
    "payload",
    [b"\xff", b"[]", b'"not-an-error-object"', b'{"error":"wrong-type"}'],
)
def test_client_collapses_malformed_error_payloads(payload: bytes) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, content=payload)

    async def exercise() -> None:
        async with ContextClient(
            "https://context.example", "secret", transport=httpx.MockTransport(handler)
        ) as client:
            await client.search("anything", "platform")

    with pytest.raises(ContextClientError) as captured:
        asyncio.run(exercise())
    assert captured.value.code == "service_error"
    assert str(captured.value) == "The context service rejected the request."


@pytest.mark.parametrize(
    "url",
    [
        "ftp://context.example",
        "https://user:secret@context.example",
        "https://context.example/prefix",
        "https://context.example?tenant=secret",
        "https://context.example#fragment",
        "not a url",
    ],
)
def test_client_rejects_unsafe_service_urls_before_attaching_bearer(url: str) -> None:
    called = False

    def handler(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200)

    with pytest.raises(ContextClientError, match="root HTTP") as captured:
        ContextClient(url, "secret", transport=httpx.MockTransport(handler))

    assert captured.value.code == "configuration_error"
    assert url not in str(captured.value)
    assert called is False

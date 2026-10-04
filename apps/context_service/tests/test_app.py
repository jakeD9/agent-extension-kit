from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from team_agent_auth import StaticBearerAuthenticator
from team_agent_contracts import Principal
from team_context_service import AppDependencies, build_app, load_content_pack

EXTENSION_PATH = Path(__file__).parents[3] / "extension"


@pytest.fixture
def client() -> TestClient:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")
    authenticator = StaticBearerAuthenticator(
        {
            "engineering-token": Principal(
                id="dev-1", groups=["engineering"], projects=["event-ingestion"]
            ),
            "outsider-token": Principal(id="dev-2", groups=["other"], projects=["other"]),
        }
    )
    return TestClient(build_app(AppDependencies(pack=pack, authenticator=authenticator)))


def test_returns_cited_knowledge_to_authorized_caller(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge/search",
        headers={"authorization": "Bearer engineering-token"},
        json={"query": "idempotency vendor identifier", "project": "event-ingestion"},
    )

    assert response.status_code == 200
    assert response.json()["results"][0]["citation"] == {
        "repository": "agent-extension-kit-sample",
        "path": "knowledge/architecture/idempotency.md",
        "revision": "fixture-revision",
        "heading": "Stable event identity",
    }
    assert response.headers["x-request-id"] == response.json()["requestId"]


def test_returns_no_content_across_authorization_boundary(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge/search",
        headers={"authorization": "Bearer outsider-token"},
        json={"query": "idempotency", "project": "event-ingestion"},
    )

    assert response.status_code == 200
    assert response.json()["results"] == []


def test_uses_canonical_error_for_invalid_authentication(client: TestClient) -> None:
    response = client.post("/v1/knowledge/search", json={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert isinstance(response.json()["error"]["requestId"], str)


def test_rejects_invalid_authenticated_request(client: TestClient) -> None:
    response = client.post(
        "/v1/knowledge/search",
        headers={"authorization": "Bearer engineering-token"},
        json={"query": "", "project": "event-ingestion"},
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_health_and_readiness_report_loaded_pack(client: TestClient) -> None:
    assert client.get("/health").json() == {"status": "healthy"}
    ready = client.get("/ready")
    assert ready.status_code == 200
    assert ready.json() == {
        "status": "ready",
        "contentPack": "agent-extension-kit-sample",
        "chunks": 3,
    }


def test_lifecycle_runs_and_dependency_failure_blocks_readiness() -> None:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")
    authenticator = StaticBearerAuthenticator({})
    events: list[str] = []

    async def startup() -> None:
        events.append("started")

    async def shutdown() -> None:
        events.append("stopped")

    async def readiness() -> tuple[str, dict[str, object] | None]:
        return "not_ready", {"mongodb": "unavailable"}

    app = build_app(
        AppDependencies(
            pack=pack,
            authenticator=authenticator,
            readiness=readiness,
            startup=startup,
            shutdown=shutdown,
        )
    )

    with TestClient(app) as lifecycle_client:
        response = lifecycle_client.get("/ready")
        assert events == ["started"]
        assert response.status_code == 503
        assert response.json()["details"] == {"mongodb": "unavailable"}

    assert events == ["started", "stopped"]

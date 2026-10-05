import base64
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from team_agent_auth import StaticBearerAuthenticator
from team_agent_contracts import Principal, SkillPackageResponse, SkillResolveResponse
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
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_lists_authorized_skill_metadata_without_body(client: TestClient) -> None:
    response = client.get(
        "/v1/skills?project=event-ingestion&limit=10",
        headers={"authorization": "Bearer engineering-token"},
    )

    assert response.status_code == 200
    assert response.json()["items"] == [
        {
            "name": "diagnose-and-fix",
            "description": (
                "Use when an error or incident must be diagnosed and repaired in a repository."
            ),
            "version": "1",
            "projects": ["event-ingestion"],
            "allowed_tools": ["search_team_knowledge", "get_team_document"],
            "citation": {
                "repository": "agent-extension-kit-sample",
                "path": "skills/diagnose-and-fix/SKILL.md",
                "revision": "fixture-revision",
            },
        }
    ]
    assert "body" not in response.json()["items"][0]
    assert response.headers["x-request-id"] == response.json()["request_id"]


def test_content_routes_fail_closed_when_knowledge_and_skill_revisions_are_incoherent() -> None:
    pack = load_content_pack(EXTENSION_PATH, "skill-revision-a")
    auth = StaticBearerAuthenticator(
        {"token": Principal(id="developer", projects=["event-ingestion"])}
    )

    async def mismatched_projection() -> tuple[str, dict[str, object]]:
        return "not_ready", {"source_revision": "knowledge-revision-b"}

    app = build_app(
        AppDependencies(
            pack=pack,
            authenticator=auth,
            readiness=mismatched_projection,
        )
    )
    with TestClient(app) as guarded:
        knowledge = guarded.post(
            "/v1/knowledge/search",
            headers={"authorization": "Bearer token"},
            json={"query": "idempotency", "project": "event-ingestion"},
        )
        skills = guarded.get(
            "/v1/skills?project=event-ingestion",
            headers={"authorization": "Bearer token"},
        )
        health = guarded.get("/health")

    assert knowledge.status_code == skills.status_code == 503
    assert knowledge.json()["error"]["code"] == "service_unavailable"
    assert health.status_code == 200


def test_loads_authorized_skill_body_with_revision_provenance(client: TestClient) -> None:
    response = client.get(
        "/v1/skills/diagnose-and-fix?project=event-ingestion&revision=fixture-revision",
        headers={"authorization": "Bearer engineering-token"},
    )

    assert response.status_code == 200
    assert response.json()["body"].startswith("# Diagnose and fix")
    assert response.json()["citation"] == {
        "repository": "agent-extension-kit-sample",
        "path": "skills/diagnose-and-fix/SKILL.md",
        "revision": "fixture-revision",
    }


def test_resolves_and_downloads_exact_immutable_skill_package(client: TestClient) -> None:
    resolved = client.post(
        "/v1/skills:resolve",
        headers={"authorization": "Bearer engineering-token"},
        json={
            "project": "event-ingestion",
            "names": ["diagnose-and-fix"],
            "revision": "fixture-revision",
        },
    )

    assert resolved.status_code == 200
    manifest = resolved.json()["manifest"]
    assert manifest["catalog_revision"] == "fixture-revision"
    assert manifest["selected_names"] == ["diagnose-and-fix"]
    assert [package["name"] for package in manifest["packages"]] == ["diagnose-and-fix"]
    package = manifest["packages"][0]
    assert package["package_id"].startswith("sha256:")
    assert package["files"][0]["path"] == "SKILL.md"

    downloaded = client.get(
        f"/v1/skill-packages/{package['package_id']}?project=event-ingestion",
        headers={"authorization": "Bearer engineering-token"},
    )
    assert downloaded.status_code == 200
    assert downloaded.json()["manifest"] == package
    assert base64.b64decode(downloaded.json()["files"][0]["content_base64"]).startswith(b"---\n")
    resolved_contract = SkillResolveResponse.model_validate_json(resolved.text)
    package_contract = SkillPackageResponse.model_validate_json(downloaded.text)
    assert resolved_contract.model_dump(mode="json", exclude_none=True) == resolved.json()
    assert package_contract.model_dump(mode="json", exclude_none=True) == downloaded.json()
    unsupported = resolved.json()
    unsupported["manifest"]["schema_version"] = "2"
    with pytest.raises(ValidationError):
        SkillResolveResponse.model_validate(unsupported)
    empty_revision = resolved.json()
    empty_revision["manifest"]["catalog_revision"] = ""
    with pytest.raises(ValidationError):
        SkillResolveResponse.model_validate(empty_revision)


def test_exact_unavailable_skill_revision_never_falls_forward(client: TestClient) -> None:
    response = client.get(
        "/v1/skills/diagnose-and-fix?project=event-ingestion&revision=missing-revision",
        headers={"authorization": "Bearer engineering-token"},
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "skill_revision_not_found"


def test_unauthorized_skill_name_does_not_reveal_revision_availability(
    client: TestClient,
) -> None:
    response = client.post(
        "/v1/skills:resolve",
        headers={"authorization": "Bearer outsider-token"},
        json={
            "project": "other",
            "names": ["diagnose-and-fix"],
            "revision": "missing-revision",
        },
    )

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "skill_not_found"


def test_resolve_all_is_scoped_and_records_explicit_roots(client: TestClient) -> None:
    response = client.post(
        "/v1/skills:resolve",
        headers={"authorization": "Bearer engineering-token"},
        json={"project": "event-ingestion", "all": True},
    )

    assert response.status_code == 200
    assert response.json()["manifest"]["selected_names"] == ["diagnose-and-fix"]


@pytest.mark.parametrize(
    "body",
    [
        {"project": "event-ingestion"},
        {"project": "event-ingestion", "names": ["diagnose-and-fix"], "all": True},
        {"project": "event-ingestion", "names": ["Not Safe"]},
        {"project": "event-ingestion", "names": [f"skill-{index}" for index in range(101)]},
    ],
)
def test_rejects_invalid_skill_resolution_selections(
    client: TestClient, body: dict[str, object]
) -> None:
    response = client.post(
        "/v1/skills:resolve",
        headers={"authorization": "Bearer engineering-token"},
        json=body,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_resolve_requires_authentication_before_body_validation(client: TestClient) -> None:
    response = client.post("/v1/skills:resolve", json={})

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_resolve_fails_when_exact_revision_or_named_skill_is_unavailable(
    client: TestClient,
) -> None:
    missing_revision = client.post(
        "/v1/skills:resolve",
        headers={"authorization": "Bearer engineering-token"},
        json={
            "project": "event-ingestion",
            "names": ["diagnose-and-fix"],
            "revision": "missing",
        },
    )
    missing_skill = client.post(
        "/v1/skills:resolve",
        headers={"authorization": "Bearer engineering-token"},
        json={"project": "event-ingestion", "names": ["not-present"]},
    )

    assert missing_revision.status_code == 404
    assert missing_revision.json()["error"]["code"] == "skill_revision_not_found"
    assert missing_skill.status_code == 404
    assert missing_skill.json()["error"]["code"] == "skill_not_found"


def test_package_lookup_does_not_leak_across_authorization_scopes(client: TestClient) -> None:
    resolved = client.post(
        "/v1/skills:resolve",
        headers={"authorization": "Bearer engineering-token"},
        json={"project": "event-ingestion", "all": True},
    ).json()
    package_id = resolved["manifest"]["packages"][0]["package_id"]

    unauthorized = client.get(
        f"/v1/skill-packages/{package_id}?project=other",
        headers={"authorization": "Bearer outsider-token"},
    )
    missing = client.get(
        f"/v1/skill-packages/sha256:{'0' * 64}?project=other",
        headers={"authorization": "Bearer outsider-token"},
    )

    assert unauthorized.status_code == missing.status_code == 404
    assert unauthorized.json()["error"]["code"] == "skill_package_not_found"
    assert unauthorized.json()["error"]["message"] == missing.json()["error"]["message"]


def test_package_lookup_requires_authentication_before_validation(client: TestClient) -> None:
    response = client.get("/v1/skill-packages/not-a-package")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


def test_skill_lookup_does_not_leak_names_across_authorization_scopes(
    client: TestClient,
) -> None:
    missing = client.get(
        "/v1/skills/not-present?project=other",
        headers={"authorization": "Bearer outsider-token"},
    )
    unauthorized_existing = client.get(
        "/v1/skills/diagnose-and-fix?project=other",
        headers={"authorization": "Bearer outsider-token"},
    )

    assert missing.status_code == unauthorized_existing.status_code == 404
    assert missing.json()["error"]["code"] == "skill_not_found"
    assert unauthorized_existing.json()["error"]["code"] == "skill_not_found"
    assert missing.json()["error"]["message"] == unauthorized_existing.json()["error"]["message"]


@pytest.mark.parametrize(
    "path",
    [
        "/v1/skills",
        "/v1/skills?project=event-ingestion&limit=51",
        "/v1/skills?project=event-ingestion&cursor=not-a-cursor",
        "/v1/skills/diagnose-and-fix",
    ],
)
def test_rejects_invalid_skill_requests_with_canonical_error(client: TestClient, path: str) -> None:
    response = client.get(path, headers={"authorization": "Bearer engineering-token"})

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    assert response.headers["x-request-id"] == response.json()["error"]["request_id"]


def test_skill_endpoints_require_bearer_authentication(client: TestClient) -> None:
    response = client.get("/v1/skills?project=event-ingestion")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"


@pytest.mark.parametrize(
    ("method", "path", "body"),
    [
        (
            "POST",
            "/v1/knowledge/search",
            {"query": "idempotency", "project": "p" * 201},
        ),
        ("GET", f"/v1/skills/{'s' * 129}?project=event-ingestion", None),
        (
            "GET",
            f"/v1/skills/diagnose-and-fix?project=event-ingestion&revision={'r' * 257}",
            None,
        ),
    ],
)
def test_model_facing_scope_identifiers_are_bounded(
    client: TestClient, method: str, path: str, body: dict[str, str] | None
) -> None:
    response = client.request(
        method,
        path,
        headers={"authorization": "Bearer engineering-token"},
        json=body,
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"


def test_skill_failure_uses_canonical_internal_error() -> None:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")

    class FailingCatalog:
        async def list(self, request: object, principal: Principal) -> object:
            raise RuntimeError("secret storage detail")

        async def get(self, name: str, project: str, principal: Principal) -> None:
            raise RuntimeError("secret storage detail")

    app = build_app(
        AppDependencies(
            pack=pack,
            authenticator=StaticBearerAuthenticator(
                {
                    "engineering-token": Principal(
                        id="dev-1", groups=["engineering"], projects=["event-ingestion"]
                    )
                }
            ),
            skill_catalog=FailingCatalog(),  # type: ignore[arg-type]
        )
    )
    failure_client = TestClient(app, raise_server_exceptions=False)

    response = failure_client.get(
        "/v1/skills?project=event-ingestion",
        headers={"authorization": "Bearer engineering-token"},
    )

    assert response.status_code == 500
    assert response.json()["error"]["code"] == "internal_error"
    assert "secret" not in response.text


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
    assert isinstance(response.json()["error"]["request_id"], str)


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
        "content_pack": "agent-extension-kit-sample",
        "chunks": 3,
    }


def test_mongo_failure_blocks_readiness_and_all_content_discovery() -> None:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")
    authenticator = StaticBearerAuthenticator(
        {
            "engineering-token": Principal(
                id="dev-1", groups=["engineering"], projects=["event-ingestion"]
            )
        }
    )
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
        skill_response = lifecycle_client.get(
            "/v1/skills/diagnose-and-fix?project=event-ingestion",
            headers={"authorization": "Bearer engineering-token"},
        )
        assert skill_response.status_code == 503
        assert skill_response.json()["error"]["code"] == "service_unavailable"

    assert events == ["started", "stopped"]

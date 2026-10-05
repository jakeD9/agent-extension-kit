from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from team_agent_auth import StaticBearerAuthenticator
from team_agent_contracts import Principal, SharedMemoryCreateRequest
from team_context_core import InMemorySharedMemory
from team_context_service import AppDependencies, build_app, load_content_pack

EXTENSION_PATH = Path(__file__).parents[3] / "extension"


def _client(memory: InMemorySharedMemory | None = None) -> TestClient:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")
    auth = StaticBearerAuthenticator(
        {
            "developer": Principal(
                id="developer-1", groups=["engineering"], projects=["event-ingestion"]
            ),
            "teammate": Principal(
                id="developer-2", groups=["different-group"], projects=["event-ingestion"]
            ),
            "outsider": Principal(id="outsider", groups=["engineering"], projects=["other"]),
        }
    )
    return TestClient(
        build_app(
            AppDependencies(
                pack=pack, authenticator=auth, shared_memory=memory or InMemorySharedMemory()
            )
        )
    )


def _memory_body(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "project": "event-ingestion",
        "title": "Vendor retries require a stable event key",
        "body": "Use the vendor event identifier as the idempotency key.",
        "provenance": {"repository": "org/incidents", "path": "INC-42.md", "revision": "abc123"},
        "evidence": [
            {
                "repository": "org/service",
                "path": "tests/test_retries.py",
                "revision": "def456",
                "heading": "duplicate delivery",
            }
        ],
        "expires_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
    }
    body.update(changes)
    return body


def _create(client: TestClient, *, key: str = "create-key") -> dict[str, object]:
    response = client.post(
        "/v1/memories",
        headers={"authorization": "Bearer developer", "idempotency-key": key},
        json=_memory_body(),
    )
    assert response.status_code == 201
    return response.json()["memory"]  # type: ignore[no-any-return]


def _update_body(memory: dict[str, object], **changes: object) -> dict[str, object]:
    body = _memory_body(**changes)
    body.pop("project")
    body["expected_revision"] = memory["revision"]
    return body


def test_developer_creates_immediately_searchable_supplemental_memory() -> None:
    client = _client()
    memory = _create(client)
    search = client.post(
        "/v1/memories/search",
        headers={"authorization": "Bearer teammate"},
        json={"query": "stable event key", "project": "event-ingestion"},
    )
    assert memory["canonicality"] == "supplemental"
    assert memory["provenance"] == _memory_body()["provenance"]
    assert search.status_code == 200
    assert [item["id"] for item in search.json()["items"]] == [memory["id"]]


def test_create_is_idempotent_and_conflicting_key_reuse_fails() -> None:
    client = _client()
    headers = {"authorization": "Bearer developer", "idempotency-key": "same-key"}
    body = _memory_body()
    first = client.post("/v1/memories", headers=headers, json=body)
    replay = client.post("/v1/memories", headers=headers, json=body)
    conflict = client.post(
        "/v1/memories", headers=headers, json={**body, "title": "Different claim"}
    )
    assert first.status_code == replay.status_code == 201
    assert first.json()["memory"] == replay.json()["memory"]
    assert conflict.status_code == 409


def test_any_project_member_can_update_and_expire_with_cas_and_audit() -> None:
    client = _client()
    memory = _create(client)
    update_body = _update_body(memory, title="Updated retry guidance")
    updated_response = client.put(
        f"/v1/memories/{memory['id']}",
        headers={"authorization": "Bearer teammate", "idempotency-key": "update-key"},
        json=update_body,
    )
    updated = updated_response.json()["memory"]
    stale = client.put(
        f"/v1/memories/{memory['id']}",
        headers={"authorization": "Bearer developer", "idempotency-key": "stale-key"},
        json=update_body,
    )
    expired = client.post(
        f"/v1/memories/{memory['id']}/expire",
        headers={"authorization": "Bearer developer", "idempotency-key": "expire-key"},
        json={"expected_revision": updated["revision"], "reason": "Vendor changed."},
    )
    audit = client.get(
        f"/v1/memories/{memory['id']}/audit?project=event-ingestion",
        headers={"authorization": "Bearer teammate"},
    )
    assert updated_response.status_code == 200
    assert updated["last_modified_by"] == "developer-2"
    assert stale.status_code == 409
    assert expired.status_code == 200
    assert [item["action"] for item in audit.json()["items"]] == ["created", "updated", "expired"]


def test_create_can_atomically_supersede_current_memory() -> None:
    client = _client()
    old = _create(client, key="old")
    replacement = client.post(
        "/v1/memories",
        headers={"authorization": "Bearer teammate", "idempotency-key": "replacement"},
        json=_memory_body(
            title="Retries use the delivery token",
            body="Use the delivery token after migration.",
            supersedes_memory_id=old["id"],
        ),
    ).json()["memory"]
    search = client.post(
        "/v1/memories/search",
        headers={"authorization": "Bearer developer"},
        json={"query": "delivery token migration", "project": "event-ingestion"},
    )
    assert [item["id"] for item in search.json()["items"]] == [replacement["id"]]


def test_outsider_cannot_create_and_cannot_discover_existing_memory() -> None:
    client = _client()
    memory = _create(client)
    denied_create = client.post(
        "/v1/memories",
        headers={"authorization": "Bearer outsider", "idempotency-key": "outside"},
        json=_memory_body(),
    )
    hidden = client.put(
        f"/v1/memories/{memory['id']}",
        headers={"authorization": "Bearer outsider", "idempotency-key": "outside-update"},
        json=_update_body(memory),
    )
    missing = client.put(
        "/v1/memories/missing",
        headers={"authorization": "Bearer outsider", "idempotency-key": "missing-update"},
        json=_update_body(memory),
    )
    assert denied_create.status_code == 403
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"]


def test_memory_api_authenticates_before_validation_and_removes_approval_routes() -> None:
    client = _client()
    assert client.post("/v1/memories", json={}).status_code == 401
    openapi = client.get("/openapi.json").json()
    assert "/v1/memories" in openapi["paths"]
    assert "/v1/memories/{memory_id}" in openapi["paths"]
    assert not any("memory-proposals" in path for path in openapi["paths"])


def test_memory_create_rejects_offset_naive_expiration() -> None:
    client = _client()
    response = client.post(
        "/v1/memories",
        headers={"authorization": "Bearer developer", "idempotency-key": "naive"},
        json=_memory_body(expires_at="2027-01-01T00:00:00"),
    )
    assert response.status_code == 422
    with pytest.raises(ValidationError):
        SharedMemoryCreateRequest.model_validate(_memory_body(expires_at="2027-01-01T00:00:00"))

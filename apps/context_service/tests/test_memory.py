from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from team_agent_auth import StaticBearerAuthenticator
from team_agent_contracts import MemoryProposalCreateRequest, Principal
from team_context_core import InMemoryGovernedMemory
from team_context_service import AppDependencies, build_app, load_content_pack

EXTENSION_PATH = Path(__file__).parents[3] / "extension"


def _client(memory: InMemoryGovernedMemory | None = None) -> TestClient:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")
    auth = StaticBearerAuthenticator(
        {
            "author": Principal(
                id="author-1", groups=["engineering"], projects=["event-ingestion"]
            ),
            "approver": Principal(
                id="approver-1",
                groups=["engineering"],
                projects=["event-ingestion"],
                roles=["approver"],
            ),
            "multi-author": Principal(
                id="author-2",
                groups=["engineering", "security"],
                projects=["event-ingestion"],
            ),
            "multi-approver": Principal(
                id="approver-2",
                groups=["engineering", "security"],
                projects=["event-ingestion"],
                roles=["approver"],
            ),
            "partial-approver": Principal(
                id="approver-3",
                groups=["engineering"],
                projects=["event-ingestion"],
                roles=["approver"],
            ),
            "outsider": Principal(id="outsider", groups=["other"], projects=["other"]),
        }
    )
    return TestClient(
        build_app(
            AppDependencies(
                pack=pack,
                authenticator=auth,
                governed_memory=memory or InMemoryGovernedMemory(),
            )
        )
    )


def _proposal_body(**changes: object) -> dict[str, object]:
    body: dict[str, object] = {
        "project": "event-ingestion",
        "access_groups": ["engineering"],
        "title": "Vendor retries require a stable event key",
        "body": "Use the vendor event identifier as the idempotency key.",
        "provenance": {
            "repository": "org/incidents",
            "path": "INC-42.md",
            "revision": "abc123",
        },
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


def _propose(client: TestClient, *, key: str = "proposal-key") -> dict[str, object]:
    response = client.post(
        "/v1/memory-proposals",
        headers={"authorization": "Bearer author", "idempotency-key": key},
        json=_proposal_body(),
    )
    assert response.status_code == 201
    return response.json()["proposal"]  # type: ignore[no-any-return]


def test_approved_memory_becomes_authoritative_with_preserved_sources() -> None:
    client = _client()
    proposal = _propose(client)

    approved = client.post(
        f"/v1/memory-proposals/{proposal['id']}/approve",
        headers={"authorization": "Bearer approver", "idempotency-key": "approve-key"},
        json={"expected_revision": 1, "reason": "Evidence reproduced."},
    )
    search = client.post(
        "/v1/memories/search",
        headers={"authorization": "Bearer author"},
        json={"query": "stable event key", "project": "event-ingestion"},
    )

    assert approved.status_code == 200
    assert approved.json()["memory"]["provenance"] == _proposal_body()["provenance"]
    assert approved.json()["memory"]["evidence"] == _proposal_body()["evidence"]
    assert search.status_code == 200
    assert [item["id"] for item in search.json()["items"]] == [approved.json()["memory"]["id"]]


def test_proposal_retry_is_exact_and_key_reuse_with_other_input_conflicts() -> None:
    client = _client()
    body = _proposal_body()
    first = client.post(
        "/v1/memory-proposals",
        headers={"authorization": "Bearer author", "idempotency-key": "same-key"},
        json=body,
    )
    replay = client.post(
        "/v1/memory-proposals",
        headers={"authorization": "Bearer author", "idempotency-key": "same-key"},
        json=body,
    )
    conflict = client.post(
        "/v1/memory-proposals",
        headers={"authorization": "Bearer author", "idempotency-key": "same-key"},
        json={**body, "title": "Different claim"},
    )

    assert first.status_code == replay.status_code == 201
    assert first.json()["proposal"] == replay.json()["proposal"]
    assert conflict.status_code == 409
    assert conflict.json()["error"]["code"] == "memory_conflict"


def test_model_author_cannot_promote_and_hidden_proposals_are_indistinguishable() -> None:
    client = _client()
    proposal = _propose(client)
    decision = {"expected_revision": 1, "reason": "self promotion"}

    author = client.post(
        f"/v1/memory-proposals/{proposal['id']}/approve",
        headers={"authorization": "Bearer author", "idempotency-key": "author-approve"},
        json=decision,
    )
    hidden = client.post(
        f"/v1/memory-proposals/{proposal['id']}/approve",
        headers={"authorization": "Bearer outsider", "idempotency-key": "outside-approve"},
        json=decision,
    )
    missing = client.post(
        "/v1/memory-proposals/missing/approve",
        headers={"authorization": "Bearer outsider", "idempotency-key": "missing-approve"},
        json=decision,
    )

    assert author.status_code == 403
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json()["error"]["code"] == missing.json()["error"]["code"]


def test_rejected_and_expired_memories_are_not_authoritative_and_audit_is_append_only() -> None:
    client = _client()
    rejected = _propose(client, key="reject-proposal")
    rejection = client.post(
        f"/v1/memory-proposals/{rejected['id']}/reject",
        headers={"authorization": "Bearer approver", "idempotency-key": "reject-key"},
        json={"expected_revision": 1, "reason": "Evidence did not reproduce."},
    )
    approved_proposal = _propose(client, key="expire-proposal")
    approval = client.post(
        f"/v1/memory-proposals/{approved_proposal['id']}/approve",
        headers={"authorization": "Bearer approver", "idempotency-key": "expire-approve"},
        json={"expected_revision": 1, "reason": "Initially valid."},
    )
    memory = approval.json()["memory"]
    expiration = client.post(
        f"/v1/memories/{memory['id']}/expire",
        headers={"authorization": "Bearer approver", "idempotency-key": "expire-key"},
        json={"expected_revision": memory["revision"], "reason": "Vendor behavior changed."},
    )
    search = client.post(
        "/v1/memories/search",
        headers={"authorization": "Bearer author"},
        json={"query": "stable event key", "project": "event-ingestion"},
    )
    audit = client.get(
        f"/v1/memory-proposals/{approved_proposal['id']}/audit?project=event-ingestion",
        headers={"authorization": "Bearer author"},
    )

    assert rejection.status_code == 200
    assert rejection.json()["proposal"]["status"] == "rejected"
    assert expiration.status_code == 200
    assert search.json()["items"] == []
    assert [item["action"] for item in audit.json()["items"]] == [
        "proposed",
        "approved",
        "expired",
    ]


def test_approving_replacement_supersedes_old_memory_atomically() -> None:
    client = _client()
    old_proposal = _propose(client, key="old-proposal")
    old_memory = client.post(
        f"/v1/memory-proposals/{old_proposal['id']}/approve",
        headers={"authorization": "Bearer approver", "idempotency-key": "old-approve"},
        json={"expected_revision": 1, "reason": "valid"},
    ).json()["memory"]
    replacement_response = client.post(
        "/v1/memory-proposals",
        headers={"authorization": "Bearer author", "idempotency-key": "replacement-proposal"},
        json=_proposal_body(
            title="Retries now use the delivery token",
            body="Use the delivery token after the vendor migration.",
            supersedes_memory_id=old_memory["id"],
        ),
    )
    replacement = replacement_response.json()["proposal"]
    new_memory = client.post(
        f"/v1/memory-proposals/{replacement['id']}/approve",
        headers={"authorization": "Bearer approver", "idempotency-key": "replacement-approve"},
        json={"expected_revision": 1, "reason": "migration verified"},
    ).json()["memory"]
    search = client.post(
        "/v1/memories/search",
        headers={"authorization": "Bearer author"},
        json={"query": "vendor migration delivery token", "project": "event-ingestion"},
    )

    assert [item["id"] for item in search.json()["items"]] == [new_memory["id"]]


def test_memory_api_authenticates_before_validation_and_documents_v1_contract() -> None:
    client = _client()

    unauthenticated = client.post("/v1/memory-proposals", json={})
    missing_idempotency = client.post(
        "/v1/memory-proposals", headers={"authorization": "Bearer author"}, json={}
    )
    openapi = client.get("/openapi.json").json()

    assert unauthenticated.status_code == 401
    assert missing_idempotency.status_code == 422
    assert {
        "/v1/memories/search",
        "/v1/memory-proposals",
        "/v1/memory-proposals/{proposal_id}/approve",
        "/v1/memory-proposals/{proposal_id}/reject",
        "/v1/memories/{memory_id}/expire",
        "/v1/memory-proposals/{proposal_id}/audit",
    }.issubset(openapi["paths"])


def test_partial_group_approver_cannot_govern_multi_group_memory() -> None:
    client = _client()
    proposed = client.post(
        "/v1/memory-proposals",
        headers={"authorization": "Bearer multi-author", "idempotency-key": "multi-proposal"},
        json=_proposal_body(access_groups=["engineering", "security"]),
    ).json()["proposal"]

    denied_approval = client.post(
        f"/v1/memory-proposals/{proposed['id']}/approve",
        headers={
            "authorization": "Bearer partial-approver",
            "idempotency-key": "partial-approve",
        },
        json={"expected_revision": 1, "reason": "insufficient scope"},
    )
    approved = client.post(
        f"/v1/memory-proposals/{proposed['id']}/approve",
        headers={
            "authorization": "Bearer multi-approver",
            "idempotency-key": "multi-approve",
        },
        json={"expected_revision": 1, "reason": "full scope"},
    ).json()["memory"]
    denied_expiration = client.post(
        f"/v1/memories/{approved['id']}/expire",
        headers={
            "authorization": "Bearer partial-approver",
            "idempotency-key": "partial-expire",
        },
        json={"expected_revision": approved["revision"], "reason": "insufficient scope"},
    )

    assert denied_approval.status_code == 404
    assert denied_approval.json()["error"]["code"] == "memory_proposal_not_found"
    assert denied_expiration.status_code == 404
    assert denied_expiration.json()["error"]["code"] == "memory_not_found"


def test_memory_proposal_rejects_offset_naive_expiration() -> None:
    client = _client()
    response = client.post(
        "/v1/memory-proposals",
        headers={"authorization": "Bearer author", "idempotency-key": "naive-expiry"},
        json=_proposal_body(expires_at="2027-01-01T00:00:00"),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"
    with pytest.raises(ValidationError):
        MemoryProposalCreateRequest.model_validate(_proposal_body(expires_at="2027-01-01T00:00:00"))


@pytest.mark.parametrize(
    "changes",
    [
        {
            "provenance": {
                "repository": "org/incidents",
                "path": "p" * 2_001,
                "revision": "abc123",
            }
        },
        {
            "evidence": [
                {"repository": "org/service", "path": "test.py", "revision": f"r-{index}"}
                for index in range(21)
            ]
        },
    ],
)
def test_memory_proposal_bounds_citations_before_persistence(changes: dict[str, object]) -> None:
    client = _client()
    response = client.post(
        "/v1/memory-proposals",
        headers={"authorization": "Bearer author", "idempotency-key": "bounded-citations"},
        json=_proposal_body(**changes),
    )

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_failed"

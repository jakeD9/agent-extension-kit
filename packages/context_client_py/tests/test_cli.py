import io
import json
from datetime import UTC, datetime, timedelta

import httpx
import team_agent_context.cli as context_cli
from team_agent_context.cli import run


def test_context_search_emits_the_rest_contract_as_snake_case_json() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/knowledge/search"
        return httpx.Response(
            200,
            json={"results": [], "request_id": "request-1"},
        )

    context_cli._TEST_TRANSPORT = httpx.MockTransport(handler)
    output = io.StringIO()
    try:
        status = run(
            ["search", "nothing", "--project", "platform"],
            environ={
                "TEAM_AGENT_CONTEXT_URL": "https://context.example",
                "TEAM_AGENT_TOKEN": "secret",
            },
            stdout=output,
        )
    finally:
        context_cli._TEST_TRANSPORT = None

    assert status == 0
    assert json.loads(output.getvalue()) == {"request_id": "request-1", "results": []}


def test_context_skill_get_reports_a_safe_machine_readable_denial() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "skill_not_found",
                    "message": "secret policy detail",
                    "request_id": "request-2",
                }
            },
        )

    context_cli._TEST_TRANSPORT = httpx.MockTransport(handler)
    errors = io.StringIO()
    try:
        status = run(
            ["skill", "get", "private-skill", "--project", "restricted"],
            environ={
                "TEAM_AGENT_CONTEXT_URL": "https://context.example",
                "TEAM_AGENT_TOKEN": "super-secret",
            },
            stderr=errors,
        )
    finally:
        context_cli._TEST_TRANSPORT = None

    assert status == 2
    assert json.loads(errors.getvalue()) == {
        "error": {
            "code": "skill_not_found",
            "message": "The skill was not found or is outside the caller's authorized scope.",
        }
    }
    assert "super-secret" not in errors.getvalue()
    assert "secret policy detail" not in errors.getvalue()


def test_argument_errors_are_json_instead_of_usage_or_system_exit() -> None:
    errors = io.StringIO()

    status = run(["search", "query"], environ={}, stderr=errors)

    assert status == 2
    assert json.loads(errors.getvalue()) == {
        "error": {
            "code": "invalid_arguments",
            "message": "The command arguments are invalid; use --help for supported options.",
        }
    }
    assert "usage:" not in errors.getvalue()


def test_memory_search_cli_preserves_rest_shape_and_cursor() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/memories/search"
        assert json.loads(request.content) == {
            "query": "retry key",
            "project": "platform",
            "limit": 5,
            "cursor": "opaque",
        }
        return httpx.Response(
            200,
            json={"items": [], "next_cursor": "next", "request_id": "memory-request"},
        )

    context_cli._TEST_TRANSPORT = httpx.MockTransport(handler)
    output = io.StringIO()
    try:
        status = run(
            [
                "memory",
                "search",
                "retry key",
                "--project",
                "platform",
                "--limit",
                "5",
                "--cursor",
                "opaque",
            ],
            environ={
                "TEAM_AGENT_CONTEXT_URL": "https://context.example",
                "TEAM_AGENT_TOKEN": "secret",
            },
            stdout=output,
        )
    finally:
        context_cli._TEST_TRANSPORT = None

    assert status == 0
    assert json.loads(output.getvalue())["next_cursor"] == "next"


def test_memory_create_cli_sends_idempotency_and_supplemental_marker() -> None:
    expires_at = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    memory = {
        "project": "platform",
        "title": "Stable retry key",
        "body": "Use the delivery identifier.",
        "provenance": {"repository": "org/incidents", "path": "INC.md", "revision": "a"},
        "evidence": [{"repository": "org/service", "path": "test.py", "revision": "b"}],
        "expires_at": expires_at,
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/v1/memories"
        assert request.headers["idempotency-key"] == "create-key"
        response_memory = {
            "schema_version": "1",
            "id": "memory-1",
            **memory,
            "author_id": "agent",
            "last_modified_by": "agent",
            "canonicality": "supplemental",
            "created_at": datetime.now(UTC).isoformat(),
            "updated_at": datetime.now(UTC).isoformat(),
            "revision": 1,
        }
        return httpx.Response(201, json={"memory": response_memory, "request_id": "memory-request"})

    context_cli._TEST_TRANSPORT = httpx.MockTransport(handler)
    output = io.StringIO()
    try:
        status = run(
            [
                "memory",
                "create",
                "--memory-json",
                json.dumps(memory),
                "--idempotency-key",
                "create-key",
            ],
            environ={
                "TEAM_AGENT_CONTEXT_URL": "https://context.example",
                "TEAM_AGENT_TOKEN": "secret",
            },
            stdout=output,
        )
    finally:
        context_cli._TEST_TRANSPORT = None

    assert status == 0
    assert json.loads(output.getvalue())["memory"]["canonicality"] == "supplemental"

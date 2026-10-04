import io
import json

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

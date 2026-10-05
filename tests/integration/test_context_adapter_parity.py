import asyncio
import io
import json
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from mcp import Client
from team_agent_auth import StaticBearerAuthenticator
from team_agent_context import ContextClient
from team_agent_context import cli as context_cli
from team_agent_context.cli import run
from team_agent_context.mcp import build_mcp_server
from team_agent_contracts import Principal
from team_context_service import AppDependencies, build_app, load_content_pack

EXTENSION_PATH = Path(__file__).parents[2] / "extension"
AUTHORIZATION = {"authorization": "Bearer engineering-token"}


@pytest.fixture
def app() -> FastAPI:
    pack = load_content_pack(EXTENSION_PATH, "fixture-revision")
    return build_app(
        AppDependencies(
            pack=pack,
            authenticator=StaticBearerAuthenticator(
                {
                    "engineering-token": Principal(
                        id="dev-1",
                        groups=["engineering"],
                        projects=["event-ingestion"],
                    ),
                    "outsider-token": Principal(
                        id="dev-2",
                        groups=["other"],
                        projects=["other"],
                    ),
                }
            ),
        )
    )


def _normalize(value: object) -> object:
    if isinstance(value, dict):
        return {
            key: _normalize(item)
            for key, item in value.items()
            if key != "request_id" and item is not None
        }
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


@pytest.mark.parametrize(
    ("rest_method", "rest_path", "rest_json", "cli_args", "tool_name", "tool_args"),
    [
        (
            "POST",
            "/v1/knowledge/search",
            {"query": "idempotency", "project": "event-ingestion", "limit": 3},
            ["search", "idempotency", "--project", "event-ingestion", "--limit", "3"],
            "search_team_knowledge",
            {"query": "idempotency", "project": "event-ingestion", "limit": 3},
        ),
        (
            "GET",
            "/v1/skills?project=event-ingestion&limit=10",
            None,
            ["skill", "list", "--project", "event-ingestion", "--limit", "10"],
            "list_team_skills",
            {"project": "event-ingestion", "limit": 10},
        ),
        (
            "GET",
            "/v1/skills/diagnose-and-fix?project=event-ingestion",
            None,
            ["skill", "get", "diagnose-and-fix", "--project", "event-ingestion"],
            "get_team_skill",
            {"name": "diagnose-and-fix", "project": "event-ingestion"},
        ),
    ],
)
def test_rest_cli_and_mcp_emit_equivalent_snake_case_contracts(
    app: FastAPI,
    rest_method: str,
    rest_path: str,
    rest_json: dict[str, object] | None,
    cli_args: list[str],
    tool_name: str,
    tool_args: dict[str, object],
) -> None:
    with TestClient(app) as rest:
        rest_response = rest.request(rest_method, rest_path, headers=AUTHORIZATION, json=rest_json)
    assert rest_response.status_code == 200

    context_cli._TEST_TRANSPORT = httpx.ASGITransport(app=app)
    cli_output = io.StringIO()
    try:
        assert (
            run(
                cli_args,
                environ={
                    "TEAM_AGENT_CONTEXT_URL": "http://context.test",
                    "TEAM_AGENT_TOKEN": "engineering-token",
                },
                stdout=cli_output,
            )
            == 0
        )
    finally:
        context_cli._TEST_TRANSPORT = None

    async def call_mcp() -> dict[str, object]:
        async with (
            ContextClient(
                "http://context.test",
                "engineering-token",
                transport=httpx.ASGITransport(app=app),
            ) as gateway,
            Client(build_mcp_server(gateway)) as mcp,
        ):
            result = await mcp.call_tool(tool_name, tool_args)
            return result.structured_content or {}

    expected = _normalize(rest_response.json())
    cli_value = _normalize(json.loads(cli_output.getvalue()))
    mcp_value = _normalize(asyncio.run(call_mcp()))
    assert cli_value == expected
    assert mcp_value == expected
    assert all("camelCase" not in json.dumps(value) for value in (expected, cli_value, mcp_value))


def test_hidden_and_missing_skills_are_indistinguishable_across_rest_cli_and_mcp(
    app: FastAPI,
) -> None:
    cases = [
        ("diagnose-and-fix", "outsider-token"),
        ("absent-skill", "engineering-token"),
    ]
    rest_errors: list[object] = []
    cli_errors: list[object] = []
    mcp_errors: list[str] = []

    for name, token in cases:
        with TestClient(app) as rest:
            response = rest.get(
                f"/v1/skills/{name}?project=event-ingestion",
                headers={"authorization": f"Bearer {token}"},
            )
        assert response.status_code == 404
        rest_errors.append(_normalize(response.json()))

        context_cli._TEST_TRANSPORT = httpx.ASGITransport(app=app)
        errors = io.StringIO()
        try:
            assert (
                run(
                    ["skill", "get", name, "--project", "event-ingestion"],
                    environ={
                        "TEAM_AGENT_CONTEXT_URL": "http://context.test",
                        "TEAM_AGENT_TOKEN": token,
                    },
                    stderr=errors,
                )
                == 2
            )
        finally:
            context_cli._TEST_TRANSPORT = None
        cli_errors.append(json.loads(errors.getvalue()))

        async def call_mcp(skill_name: str = name, bearer_token: str = token) -> str:
            async with (
                ContextClient(
                    "http://context.test",
                    bearer_token,
                    transport=httpx.ASGITransport(app=app),
                ) as gateway,
                Client(build_mcp_server(gateway)) as mcp,
            ):
                result = await mcp.call_tool(
                    "get_team_skill",
                    {"name": skill_name, "project": "event-ingestion"},
                )
                assert result.is_error
                return result.content[0].text  # type: ignore[union-attr]

        mcp_errors.append(asyncio.run(call_mcp()))

    assert rest_errors[0] == rest_errors[1]
    assert cli_errors[0] == cli_errors[1]
    assert mcp_errors[0] == mcp_errors[1]

import asyncio
import sys
from pathlib import Path
from xml.etree import ElementTree

import httpx
from mcp import Client, StdioServerParameters
from mcp_types import ListToolsResult
from team_agent_context.client import ContextClient
from team_agent_context.mcp import build_mcp_server


def _transport() -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/knowledge/search":
            return httpx.Response(
                200,
                json={
                    "results": [],
                    "request_id": "request-search",
                },
            )
        if request.url.path == "/v1/skills":
            assert (
                request.url.params.get("cursor") == "opaque-next"
                or "cursor" not in request.url.params
            )
            return httpx.Response(
                200,
                json={
                    "items": [],
                    "next_cursor": "opaque-after-this-page",
                    "request_id": "request-list",
                },
            )
        if request.url.path == "/v1/skills/diagnose-and-fix":
            return httpx.Response(
                200,
                json={
                    "name": "diagnose-and-fix",
                    "description": "Diagnose a repository failure.",
                    "version": "1",
                    "projects": ["platform"],
                    "access_groups": ["engineering"],
                    "allowed_tools": ["search_team_knowledge"],
                    "citation": {
                        "repository": "org/context",
                        "path": "skills/diagnose-and-fix/SKILL.md",
                        "revision": "rev-1",
                    },
                    "body": "Investigate before editing.",
                    "request_id": "request-get",
                },
            )
        raise AssertionError(request.url.path)

    return httpx.MockTransport(handler)


def test_mcp_registers_three_model_oriented_tools_with_opaque_pagination() -> None:
    async def exercise() -> tuple[list[str], dict[str, object]]:
        async with ContextClient(
            "https://context.example", "secret", transport=_transport()
        ) as gateway:
            server = build_mcp_server(gateway)
            async with Client(server) as client:
                listed = await client.list_tools()
                result = await client.call_tool(
                    "list_team_skills",
                    {"project": "platform", "limit": 10, "cursor": "opaque-next"},
                )
                return [tool.name for tool in listed.tools], result.structured_content or {}

    names, result = asyncio.run(exercise())

    assert names == ["search_team_knowledge", "list_team_skills", "get_team_skill"]
    assert result == {
        "items": [],
        "next_cursor": "opaque-after-this-page",
        "request_id": "request-list",
    }


def test_mcp_get_skill_preserves_snake_case_rest_shape() -> None:
    async def exercise() -> dict[str, object]:
        async with (
            ContextClient("https://context.example", "secret", transport=_transport()) as gateway,
            Client(build_mcp_server(gateway)) as client,
        ):
            result = await client.call_tool(
                "get_team_skill",
                {"name": "diagnose-and-fix", "project": "platform"},
            )
            return result.structured_content or {}

    result = asyncio.run(exercise())

    assert result["access_groups"] == ["engineering"]
    assert result["allowed_tools"] == ["search_team_knowledge"]
    assert result["citation"] == {
        "repository": "org/context",
        "path": "skills/diagnose-and-fix/SKILL.md",
        "revision": "rev-1",
        "heading": None,
    }


def test_mcp_denial_is_actionable_and_does_not_disclose_upstream_detail() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            404,
            json={
                "error": {
                    "code": "skill_not_found",
                    "message": "restricted skill exists for another group",
                    "request_id": "request-denied",
                }
            },
        )

    async def exercise() -> tuple[bool, str]:
        async with (
            ContextClient(
                "https://context.example",
                "secret",
                transport=httpx.MockTransport(handler),
            ) as gateway,
            Client(build_mcp_server(gateway)) as client,
        ):
            result = await client.call_tool(
                "get_team_skill",
                {"name": "private-skill", "project": "restricted"},
            )
            return bool(result.is_error), result.content[0].text  # type: ignore[union-attr]

    is_error, message = asyncio.run(exercise())

    assert is_error is True
    assert "skill_not_found" in message
    assert "outside the caller's authorized scope" in message
    assert "another group" not in message


def test_mcp_stdio_entrypoint_negotiates_and_lists_tools() -> None:
    async def exercise() -> list[str]:
        parameters = StdioServerParameters(
            command=sys.executable,
            args=["-m", "team_agent_context.mcp"],
            env={
                "TEAM_AGENT_CONTEXT_URL": "http://127.0.0.1:1",
                "TEAM_AGENT_TOKEN": "stdio-fixture-token",
            },
        )
        async with Client(parameters, read_timeout_seconds=10) as client:
            result = await client.list_tools()
            return [tool.name for tool in result.tools]

    assert asyncio.run(exercise()) == [
        "search_team_knowledge",
        "list_team_skills",
        "get_team_skill",
    ]


def test_owned_gateway_closes_in_the_mcp_server_lifespan() -> None:
    class TrackingContextClient(ContextClient):
        closed = False

        async def close(self) -> None:
            self.closed = True
            await super().close()

    async def exercise() -> bool:
        gateway = TrackingContextClient("https://context.example", "secret", transport=_transport())
        async with Client(build_mcp_server(gateway, close_gateway=True)) as client:
            await client.list_tools()
        return gateway.closed

    assert asyncio.run(exercise()) is True


def test_mcp_tool_descriptions_and_input_fields_guide_a_cold_start_model() -> None:
    async def exercise() -> ListToolsResult:
        async with (
            ContextClient("https://context.example", "secret", transport=_transport()) as gateway,
            Client(build_mcp_server(gateway)) as client,
        ):
            return await client.list_tools()

    result = asyncio.run(exercise())

    for tool in result.tools:
        assert tool.description is not None and len(tool.description.split()) >= 20
        properties = tool.input_schema["properties"]
        assert all("description" in schema for schema in properties.values())
    by_name = {tool.name: tool for tool in result.tools}
    search_project = by_name["search_team_knowledge"].input_schema["properties"]["project"]
    assert search_project["maxLength"] == 200
    assert by_name["get_team_skill"].input_schema["properties"]["name"]["maxLength"] == 128
    revision = by_name["get_team_skill"].input_schema["properties"]["revision"]
    assert revision["anyOf"][0]["maxLength"] == 256


def test_context_mcp_eval_set_has_ten_multi_call_questions() -> None:
    path = Path(__file__).parents[3] / "evals/context-mcp.xml"
    root = ElementTree.parse(path).getroot()
    evaluations = root.findall("eval")

    assert len(evaluations) == 10
    for evaluation in evaluations:
        expected = evaluation.findtext("expected", default="")
        calls = sum(
            expected.count(name)
            for name in (
                "search_team_knowledge",
                "list_team_skills",
                "get_team_skill",
            )
        )
        assert calls >= 2
        assert evaluation.findtext("question", default="").strip()

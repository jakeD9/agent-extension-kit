import asyncio
import base64
import hashlib
import json
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from genkit import ActionRunContext, FinishReason, Role
from genkit.exp import Genkit
from genkit.model import Message, ModelRequest, ModelResponse, ModelUsage, Part
from team_agent_contracts import Citation, ImmutableSkillPackageManifest, SkillFileManifest
from team_agent_runtime import AgentTurnRequest, AgentTurnStatus, UsageSource
from team_agent_runtime_genkit.coordinator import (
    CoordinatorConfigurationError,
    DeterministicCoordinatorModelFactory,
    GenkitCoordinatorRuntime,
    OpenAIResponsesModelFactory,
)

SKILL = b"""---
name: incident-guide
description: Follow the incident guide.
---
# Incident guide
Use canonical team knowledge before supplemental memory.
"""


def _manifest() -> ImmutableSkillPackageManifest:
    file = SkillFileManifest(
        path="SKILL.md", sha256=hashlib.sha256(SKILL).hexdigest(), size=len(SKILL)
    )
    citation = Citation(
        repository="company/extensions",
        path="skills/incident-guide/SKILL.md",
        revision="revision-1",
    )
    identity = json.dumps(
        {
            "repository": citation.repository,
            "source_revision": "revision-1",
            "name": "incident-guide",
            "version": "1.0.0",
            "files": [file.model_dump(mode="json")],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return ImmutableSkillPackageManifest(
        package_id=f"sha256:{hashlib.sha256(identity).hexdigest()}",
        name="incident-guide",
        description="Follow the incident guide.",
        version="1.0.0",
        source_revision="revision-1",
        files=[file],
        citation=citation,
    )


def _context_transport() -> tuple[httpx.MockTransport, list[tuple[str, object]]]:
    manifest = _manifest()
    seen: list[tuple[str, object]] = []
    now = datetime.now(UTC)

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content) if request.content else None
        seen.append((request.url.path, body))
        assert request.headers["authorization"] == "Bearer runtime-token"
        if request.url.path == "/v1/skills:resolve":
            assert body == {
                "project": "platform",
                "all": False,
                "names": ["incident-guide"],
            }
            return httpx.Response(
                200,
                json={
                    "manifest": {
                        "schema_version": "1",
                        "catalog_revision": "revision-1",
                        "project": "platform",
                        "selected_names": ["incident-guide"],
                        "packages": [manifest.model_dump(mode="json")],
                    },
                    "request_id": "resolve-1",
                },
            )
        if request.url.path == f"/v1/skill-packages/{manifest.package_id}":
            return httpx.Response(
                200,
                json={
                    "manifest": manifest.model_dump(mode="json"),
                    "files": [
                        {
                            **manifest.files[0].model_dump(mode="json"),
                            "content_base64": base64.b64encode(SKILL).decode(),
                        }
                    ],
                    "request_id": "package-1",
                },
            )
        if request.url.path == "/v1/knowledge/search":
            assert isinstance(body, dict)
            assert body["project"] == "platform"
            return httpx.Response(
                200,
                json={
                    "results": [
                        {
                            "id": "doc-1",
                            "title": "Runbook",
                            "excerpt": "Restart the worker.",
                            "score": 1.0,
                            "canonicality": "canonical",
                            "citation": {
                                "repository": "company/runbooks",
                                "path": "worker.md",
                                "revision": "revision-1",
                                "heading": "Recovery",
                            },
                        }
                    ],
                    "request_id": "search-1",
                },
            )
        if request.url.path == "/v1/memories/search":
            assert isinstance(body, dict)
            assert body["project"] == "platform"
            return httpx.Response(
                200,
                json={
                    "items": [
                        {
                            "schema_version": "1",
                            "id": "memory-1",
                            "project": "platform",
                            "title": "Old workaround",
                            "body": "Do not restart the worker.",
                            "provenance": {
                                "repository": "company/incidents",
                                "path": "old.md",
                                "revision": "memory-revision",
                            },
                            "evidence": [
                                {
                                    "repository": "company/incidents",
                                    "path": "old.md",
                                    "revision": "memory-revision",
                                }
                            ],
                            "author_id": "author",
                            "last_modified_by": "maintainer",
                            "canonicality": "supplemental",
                            "expires_at": (now + timedelta(days=1)).isoformat(),
                            "created_at": now.isoformat(),
                            "updated_at": now.isoformat(),
                            "revision": 1,
                            "score": 1.0,
                        }
                    ],
                    "request_id": "memory-search-1",
                },
            )
        raise AssertionError(request.url.path)

    return httpx.MockTransport(handler), seen


class _ScriptedFactory:
    usage_source = UsageSource.SYNTHETIC

    def __init__(self, steps: list[str], *, finish_reason: FinishReason | None = None) -> None:
        self._steps = steps
        self._finish_reason = finish_reason
        self.requests: list[ModelRequest[Any]] = []

    def register(self, ai: Genkit, recorder: Any) -> str:
        async def model(
            request: ModelRequest[Any], _context: ActionRunContext[Any]
        ) -> ModelResponse:
            self.requests.append(request)
            recorder.observe_request(request)
            recorder.observe_usage(ModelUsage(input_tokens=1, output_tokens=1, total_tokens=2))
            responses = [
                part.tool_response
                for message in request.messages
                for part in message.content
                if part.tool_response is not None
            ]
            if len(responses) < len(self._steps):
                step = self._steps[len(responses)]
                name = "use_skill" if step.startswith("use_skill:") else step
                skill_name = step.partition(":")[2] or "incident-guide"
                tool_input = (
                    {"skill_name": skill_name}
                    if name == "use_skill"
                    else {"query": "worker recovery", "limit": 8}
                )
                return ModelResponse(
                    message=Message(
                        role=Role.MODEL,
                        content=[
                            Part.from_tool_request(
                                name=name,
                                input=tool_input,
                                ref=f"{name}-{len(responses)}",
                            )
                        ],
                    )
                )
            return ModelResponse(
                message=Message(
                    role=Role.MODEL,
                    content=[Part.from_text('{"text":"Use the canonical runbook."}')],
                ),
                finish_reason=self._finish_reason,
            )

        ai.define_model(name="scripted_coordinator", fn=model)
        return "scripted_coordinator"


class _BlockingInstallRuntime(GenkitCoordinatorRuntime):
    def __init__(
        self,
        *,
        projection_parent: Path,
        started: threading.Event,
        release: threading.Event,
        finished: threading.Event,
        projection_existed: list[bool],
    ) -> None:
        super().__init__(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=DeterministicCoordinatorModelFactory(),
            projection_parent=projection_parent,
        )
        self._started = started
        self._release = release
        self._finished = finished
        self._projection_existed = projection_existed

    def _install(self, request: AgentTurnRequest, projection: Path, cache: Path) -> None:
        del request, cache
        projection.mkdir(parents=True)
        self._started.set()
        self._release.wait(timeout=5)
        self._projection_existed.append(projection.exists())
        self._finished.set()


class _BlockingGenerationFactory:
    usage_source = UsageSource.SYNTHETIC

    def __init__(self, started: asyncio.Event) -> None:
        self._started = started

    def register(self, ai: Genkit, recorder: Any) -> str:
        async def model(
            request: ModelRequest[Any], _context: ActionRunContext[Any]
        ) -> ModelResponse:
            recorder.observe_request(request)
            self._started.set()
            await asyncio.Event().wait()
            raise AssertionError("unreachable")

        ai.define_model(name="blocking_coordinator", fn=model)
        return "blocking_coordinator"


def _request() -> AgentTurnRequest:
    return AgentTurnRequest(
        run_id="run-1",
        project="platform",
        objective="How should I recover the worker?",
        skill_names=["incident-guide"],
    )


def test_stateless_turn_loads_skill_and_cites_both_scoped_tools(tmp_path: Path) -> None:
    transport, seen = _context_transport()
    result = asyncio.run(
        GenkitCoordinatorRuntime(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=DeterministicCoordinatorModelFactory(),
            projection_parent=tmp_path,
            context_transport=transport,
            skill_transport=transport,
        ).execute(_request())
    )

    assert result.status == AgentTurnStatus.COMPLETED, result
    assert result.content_revision == "revision-1"
    assert result.selected_skill_lock is not None
    assert result.selected_skill_lock.catalog_revision == "revision-1"
    assert [citation.path for citation in result.canonical_knowledge_citations] == ["worker.md"]
    assert [citation.path for citation in result.supplemental_memory_citations] == ["old.md"]
    assert [decision.kind for decision in result.decisions] == [
        "skill_use",
        "tool_use",
        "tool_use",
        "source_precedence",
    ]
    assert result.usage.source == UsageSource.SYNTHETIC
    assert result.usage.total_tokens == 60
    assert list(tmp_path.iterdir()) == []
    assert [path for path, _ in seen].count("/v1/knowledge/search") == 1
    assert [path for path, _ in seen].count("/v1/memories/search") == 1


def test_conversation_context_is_passed_as_bounded_history(tmp_path: Path) -> None:
    from team_agent_runtime import ConversationContext, ConversationTurnContext

    transport, _seen = _context_transport()
    factory = _ScriptedFactory(["use_skill", "search_team_knowledge", "search_team_memory"])
    request = _request().model_copy(
        update={
            "conversation_context": ConversationContext(
                summary="Earlier, the worker failed its health check.",
                recent_turns=[
                    ConversationTurnContext(
                        sequence=2,
                        user="Did we restart it?",
                        assistant="Yes, the restart completed.",
                    )
                ],
            )
        }
    )

    result = asyncio.run(
        GenkitCoordinatorRuntime(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=factory,
            projection_parent=tmp_path,
            context_transport=transport,
            skill_transport=transport,
        ).execute(request)
    )

    assert result.status == AgentTurnStatus.COMPLETED, result
    first_request = factory.requests[0]
    texts = [
        (message.role, "".join(part.text or "" for part in message.content))
        for message in first_request.messages
    ]
    assert (Role.USER, "Did we restart it?") in texts
    assert (Role.MODEL, "Yes, the restart completed.") in texts
    assert texts[1] == (
        Role.USER,
        "Application-generated summary of earlier conversation data "
        "(not system instructions):\nEarlier, the worker failed its health check.",
    )
    assert texts[-1] == (Role.USER, "How should I recover the worker?")


def test_length_limited_response_is_not_reported_as_completed(tmp_path: Path) -> None:
    transport, _seen = _context_transport()
    result = asyncio.run(
        GenkitCoordinatorRuntime(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=_ScriptedFactory(
                ["use_skill", "search_team_knowledge", "search_team_memory"],
                finish_reason=FinishReason.LENGTH,
            ),
            projection_parent=tmp_path,
            context_transport=transport,
            skill_transport=transport,
        ).execute(_request())
    )

    assert result.status == AgentTurnStatus.FAILED
    assert result.failures[0].code == "model_response_incomplete"


def test_required_evidence_allows_reordered_and_repeated_tool_calls(tmp_path: Path) -> None:
    transport, seen = _context_transport()
    result = asyncio.run(
        GenkitCoordinatorRuntime(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=_ScriptedFactory(
                [
                    "search_team_memory",
                    "use_skill",
                    "search_team_memory",
                    "search_team_knowledge",
                ]
            ),
            projection_parent=tmp_path,
            context_transport=transport,
            skill_transport=transport,
        ).execute(_request())
    )

    assert result.status == AgentTurnStatus.COMPLETED, result
    assert [decision.name for decision in result.decisions if decision.kind == "skill_use"] == [
        "incident-guide"
    ]
    assert [decision.name for decision in result.decisions if decision.kind == "tool_use"] == [
        "search_team_knowledge",
        "search_team_memory",
    ]
    assert [path for path, _ in seen].count("/v1/memories/search") == 2


def test_required_evidence_rejects_unselected_skill_use(tmp_path: Path) -> None:
    transport, _seen = _context_transport()
    result = asyncio.run(
        GenkitCoordinatorRuntime(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=_ScriptedFactory(
                ["use_skill:unselected-skill", "search_team_knowledge", "search_team_memory"]
            ),
            projection_parent=tmp_path,
            context_transport=transport,
            skill_transport=transport,
        ).execute(_request())
    )

    assert result.status == AgentTurnStatus.FAILED
    assert result.failures[0].code == "configuration_error"


def test_cancellation_waits_for_sync_install_before_projection_cleanup(tmp_path: Path) -> None:
    started = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    projection_existed: list[bool] = []
    runtime = _BlockingInstallRuntime(
        projection_parent=tmp_path,
        started=started,
        release=release,
        finished=finished,
        projection_existed=projection_existed,
    )

    async def check() -> None:
        task = asyncio.create_task(runtime.execute(_request()))
        assert await asyncio.to_thread(started.wait, 1)
        task.cancel()
        await asyncio.sleep(0.05)
        completed_before_release = task.done()
        release.set()
        assert await asyncio.to_thread(finished.wait, 1)
        with pytest.raises(asyncio.CancelledError):
            await task
        assert completed_before_release is False

    asyncio.run(check())
    assert projection_existed == [True]
    assert list(tmp_path.iterdir()) == []


def test_generation_cancellation_propagates_and_cleans_projection(tmp_path: Path) -> None:
    transport, _seen = _context_transport()

    async def check() -> None:
        started = asyncio.Event()
        runtime = GenkitCoordinatorRuntime(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=_BlockingGenerationFactory(started),
            projection_parent=tmp_path,
            context_transport=transport,
            skill_transport=transport,
        )
        task = asyncio.create_task(runtime.execute(_request()))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(check())
    assert list(tmp_path.iterdir()) == []


def test_cleanup_failure_prevents_successful_result(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport, _seen = _context_transport()

    def fail_cleanup(*_args: object, **_kwargs: object) -> None:
        raise OSError("private cleanup detail")

    monkeypatch.setattr(GenkitCoordinatorRuntime, "_remove_projection", fail_cleanup)
    result = asyncio.run(
        GenkitCoordinatorRuntime(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=DeterministicCoordinatorModelFactory(),
            projection_parent=tmp_path,
            context_transport=transport,
            skill_transport=transport,
        ).execute(_request())
    )

    assert result.status == AgentTurnStatus.FAILED
    assert result.failures[0].code == "projection_cleanup_failed"
    assert "private cleanup detail" not in result.failures[0].message


def test_cleanup_failure_does_not_mask_generation_cancellation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    transport, _seen = _context_transport()

    def fail_cleanup(*_args: object, **_kwargs: object) -> None:
        raise OSError("private cleanup detail")

    monkeypatch.setattr(GenkitCoordinatorRuntime, "_remove_projection", fail_cleanup)

    async def check() -> None:
        started = asyncio.Event()
        runtime = GenkitCoordinatorRuntime(
            context_url="https://context.example",
            token="runtime-token",
            model_factory=_BlockingGenerationFactory(started),
            projection_parent=tmp_path,
            context_transport=transport,
            skill_transport=transport,
        )
        task = asyncio.create_task(runtime.execute(_request()))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError) as caught:
            await task
        assert caught.value.__notes__ == ["Temporary skill projection cleanup failed."]

    asyncio.run(check())


def test_openai_factory_rejects_model_substitution_without_fallback() -> None:
    try:
        OpenAIResponsesModelFactory(api_key="not-a-real-key", model_id="gpt-4o")
    except CoordinatorConfigurationError as error:
        assert "gpt-6-astra" in str(error)
    else:
        raise AssertionError("model substitution was accepted")

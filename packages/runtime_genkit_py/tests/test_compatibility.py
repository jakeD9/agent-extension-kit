import asyncio
import base64
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient
from genkit import ActionKind, Role, ToolChoice
from genkit import Genkit as StableGenkit
from genkit.exp import Genkit
from genkit.model import Message, ModelRequest, Part
from team_agent_contracts import Citation, ImmutableSkillPackageManifest, SkillFileManifest
from team_agent_runtime_genkit.compatibility import (
    CompatibilityError,
    RecordingSessionStore,
    build_deterministic_runtime,
    build_fastapi_app,
    run_deterministic_probe,
    run_live_openai_probe,
    trace_inspection_commands,
)
from team_agent_runtime_genkit.openai_responses import (
    PHASE_METADATA_KEY,
    REASONING_METADATA_KEY,
    define_openai_responses_model,
)
from team_agent_skills import SkillClient, SkillInstaller

SKILL_CONTENT = b"""---
name: compat-probe
description: Proves an authorized projection reaches Genkit middleware.
---
# Compatibility probe
COMPATIBILITY_PROJECTION_MARKER
"""


def _manifest() -> ImmutableSkillPackageManifest:
    file = SkillFileManifest(
        path="SKILL.md", sha256=hashlib.sha256(SKILL_CONTENT).hexdigest(), size=len(SKILL_CONTENT)
    )
    citation = Citation(
        repository="org/context", path="skills/compat-probe/SKILL.md", revision="compat-rev-1"
    )
    identity = json.dumps(
        {
            "repository": citation.repository,
            "source_revision": "compat-rev-1",
            "name": "compat-probe",
            "version": "1.0.0",
            "files": [file.model_dump(mode="json")],
        },
        separators=(",", ":"),
        sort_keys=True,
    ).encode()
    return ImmutableSkillPackageManifest(
        package_id=f"sha256:{hashlib.sha256(identity).hexdigest()}",
        name="compat-probe",
        description="Compatibility probe",
        version="1.0.0",
        source_revision="compat-rev-1",
        files=[file],
        citation=citation,
    )


def _install_generic_projection(tmp_path: Path) -> Path:
    manifest = _manifest()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1/skills:resolve":
            return httpx.Response(
                200,
                json={
                    "manifest": {
                        "schema_version": "1",
                        "catalog_revision": "compat-rev-1",
                        "project": "platform",
                        "selected_names": [manifest.name],
                        "packages": [manifest.model_dump(mode="json")],
                    },
                    "request_id": "compat-resolve",
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
                            "content_base64": base64.b64encode(SKILL_CONTENT).decode(),
                        }
                    ],
                    "request_id": "compat-package",
                },
            )
        raise AssertionError(f"unexpected path: {request.url.path}")

    projection = tmp_path / "projection"
    with SkillClient(
        "https://context.example", "test-token", transport=httpx.MockTransport(handler)
    ) as client:
        SkillInstaller(projection, tmp_path / "cache").pull(
            client, project="platform", names=[manifest.name], target="generic"
        )
    return projection


class _FakeResponses:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        has_tool_output = any(
            item.get("type") == "function_call_output" for item in kwargs["input"]
        )
        if kwargs.get("stream"):
            if kwargs.get("tools") and not has_tool_output:
                reasoning = SimpleNamespace(
                    type="reasoning",
                    id="rs_1",
                    summary=[],
                    encrypted_content="encrypted-reasoning",
                    status="completed",
                )
                call = SimpleNamespace(
                    type="function_call",
                    name="double_value",
                    call_id="call-1",
                    arguments='{"value":21}',
                )
                return _FakeStream(
                    SimpleNamespace(output=[reasoning, call], output_text="", status="completed"),
                    [],
                )
            text = "42" if kwargs.get("tools") else "stream-ok"
            content = SimpleNamespace(type="output_text", text=text)
            final = SimpleNamespace(
                output=[SimpleNamespace(type="message", content=[content])],
                output_text=text,
                status="completed",
            )
            deltas = [] if has_tool_output else ["stream-", "ok"]
            return _FakeStream(final, deltas)
        if kwargs.get("tools") and not has_tool_output:
            call = SimpleNamespace(
                type="function_call",
                name="double_value",
                call_id="call-1",
                arguments='{"value":21}',
            )
            return SimpleNamespace(output=[call], output_text="", status="completed")
        text = '{"answer":42,"evidence":["responses-adapter"]}' if kwargs.get("text") else "42"
        content = SimpleNamespace(type="output_text", text=text)
        return SimpleNamespace(
            output=[SimpleNamespace(type="message", content=[content])],
            output_text=text,
            status="completed",
            usage=SimpleNamespace(
                input_tokens=11,
                output_tokens=7,
                total_tokens=18,
                input_tokens_details=SimpleNamespace(cached_tokens=3),
                output_tokens_details=SimpleNamespace(reasoning_tokens=2),
            ),
        )


class _FakeStream:
    def __init__(self, final: Any, deltas: list[str]) -> None:
        self.closed = False
        self.close_count = 0
        self.events = iter(
            [
                *(
                    SimpleNamespace(type="response.output_text.delta", delta=delta)
                    for delta in deltas
                ),
                SimpleNamespace(type="response.completed", response=final),
            ]
        )

    def __aiter__(self) -> "_FakeStream":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self.events)
        except StopIteration as error:
            raise StopAsyncIteration from error

    async def close(self) -> None:
        self.close_count += 1
        self.closed = True


class _FakeClient:
    def __init__(self) -> None:
        self.responses = _FakeResponses()


class _FailingResponses:
    async def create(self, **_kwargs: Any) -> Any:
        raise RuntimeError("provider-secret-must-not-leak")


class _FailingClient:
    def __init__(self) -> None:
        self.responses = _FailingResponses()


class _StalledStream:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancel_observed = asyncio.Event()
        self.close_observed = asyncio.Event()
        self.closed = False
        self.cancelled = False
        self.close_count = 0

    def __aiter__(self) -> "_StalledStream":
        return self

    async def __anext__(self) -> Any:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            self.cancel_observed.set()
            raise
        raise StopAsyncIteration

    async def close(self) -> None:
        self.close_count += 1
        self.closed = True
        self.close_observed.set()


class _StalledResponses:
    def __init__(self, stream: _StalledStream) -> None:
        self.stream = stream

    async def create(self, **_kwargs: Any) -> Any:
        return self.stream


class _StalledClient:
    def __init__(self, stream: _StalledStream) -> None:
        self.responses = _StalledResponses(stream)


class _StaticResponses:
    def __init__(self, response: Any) -> None:
        self.response = response
        self.calls: list[dict[str, Any]] = []

    async def create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return self.response


class _StaticClient:
    def __init__(self, response: Any) -> None:
        self.responses = _StaticResponses(response)


class _BlockedCreateResponses:
    def __init__(self) -> None:
        self.started = asyncio.Event()
        self.cancelled = asyncio.Event()

    async def create(self, **_kwargs: Any) -> Any:
        self.started.set()
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled.set()
            raise


class _BlockedCreateClient:
    def __init__(self, responses: _BlockedCreateResponses) -> None:
        self.responses = responses


class _EventStream:
    def __init__(self, events: list[Any]) -> None:
        self._events = iter(events)
        self.closed = False

    def __aiter__(self) -> "_EventStream":
        return self

    async def __anext__(self) -> Any:
        try:
            return next(self._events)
        except StopIteration as error:
            raise StopAsyncIteration from error

    async def close(self) -> None:
        self.closed = True


class _EventStreamResponses:
    def __init__(self, stream: _EventStream) -> None:
        self.stream = stream

    async def create(self, **_kwargs: Any) -> Any:
        return self.stream


class _EventStreamClient:
    def __init__(self, stream: _EventStream) -> None:
        self.responses = _EventStreamResponses(stream)


def test_selected_agent_path_and_complete_deterministic_gate(tmp_path: Path) -> None:
    report = asyncio.run(run_deterministic_probe(_install_generic_projection(tmp_path)))

    assert not hasattr(StableGenkit, "define_agent")
    assert hasattr(Genkit, "define_agent")
    assert report.runtime_path == "genkit.exp.Genkit.define_agent"
    assert report.tool_result == "42"
    assert report.structured_answer == 42
    assert report.streamed_text == "stream-ok"
    assert report.continuation_turns == 2
    assert report.store_reads > 0 and report.store_writes > 0
    assert report.skill_loaded is True
    assert report.abort_status == "pending->aborted"
    assert "/api/compatibility_agent" in report.fastapi_routes


def test_responses_adapter_supports_tools_structured_output_and_streaming() -> None:
    from pydantic import BaseModel, ConfigDict

    ai = Genkit()
    client = _FakeClient()
    observed_requests: list[ModelRequest[Any]] = []
    observed_usage: list[Any] = []
    model = define_openai_responses_model(
        ai,
        client=client,
        request_observer=observed_requests.append,
        usage_observer=observed_usage.append,
    )

    class ToolInput(BaseModel):
        value: int

    class Output(BaseModel):
        model_config = ConfigDict(extra="forbid")

        answer: int
        evidence: list[str]

    @ai.tool(name="double_value")
    async def double_value(input: ToolInput) -> int:
        return input.value * 2

    agent = ai.define_agent(name="responses_agent", model=model, tools=[double_value])

    async def check() -> None:
        tool_turn = agent.chat().send_stream("double")
        tool_chunks = [chunk async for chunk in tool_turn.stream]
        assert tool_chunks
        assert (await tool_turn.response).text == "42"
        structured = await ai.generate(
            model=model, prompt="structured", output_format="json", output_schema=Output
        )
        assert structured.output == Output(answer=42, evidence=["responses-adapter"])
        assert str(structured.finish_reason) == "stop"
        assert structured.usage is not None
        assert structured.usage.input_tokens == 11
        assert structured.usage.output_tokens == 7
        assert structured.usage.total_tokens == 18
        assert structured.usage.cached_content_tokens == 3
        assert structured.usage.thoughts_tokens == 2
        streamed = ai.generate_stream(model=model, prompt="stream")
        chunks = [chunk.text async for chunk in streamed.stream]
        assert "".join(chunks) == "stream-ok"
        assert (await streamed.response).text == "stream-ok"

    asyncio.run(check())
    assert len(observed_requests) == len(client.responses.calls)
    assert sum(int(usage.total_tokens or 0) for usage in observed_usage) == 18
    assert client.responses.calls[0]["model"] == "gpt-6-astra"
    assert client.responses.calls[0]["include"] == ["reasoning.encrypted_content"]
    assert client.responses.calls[0]["tools"][0]["strict"] is False
    replayed_items = client.responses.calls[1]["input"]
    reasoning_index = next(
        index for index, item in enumerate(replayed_items) if item.get("type") == "reasoning"
    )
    call_index = next(
        index for index, item in enumerate(replayed_items) if item.get("type") == "function_call"
    )
    output_index = next(
        index
        for index, item in enumerate(replayed_items)
        if item.get("type") == "function_call_output"
    )
    assert reasoning_index < call_index < output_index
    assert replayed_items[reasoning_index] == {
        "id": "rs_1",
        "summary": [],
        "type": "reasoning",
        "encrypted_content": "encrypted-reasoning",
        "status": "completed",
    }
    assert client.responses.calls[1]["input"][-1]["type"] == "function_call_output"
    assert client.responses.calls[2]["text"]["format"]["type"] == "json_schema"


def test_responses_adapter_rejects_open_map_structured_schema_without_calling_provider() -> None:
    from pydantic import BaseModel

    class OpenMapOutput(BaseModel):
        labels: dict[str, str]

    ai = Genkit()
    client = _FakeClient()
    model = define_openai_responses_model(ai, client=client)

    response = asyncio.run(
        ai.generate(
            model=model,
            prompt="structured map",
            output_format="json",
            output_schema=OpenMapOutput,
        )
    )

    assert response.error is not None
    assert "cannot be made strict" in response.error.message
    assert client.responses.calls == []


def test_responses_adapter_preserves_mixed_message_part_order() -> None:
    ai = Genkit()
    client = _FakeClient()
    model = define_openai_responses_model(ai, client=client)
    messages = [
        Message(
            role=Role.MODEL,
            content=[
                Part.from_text("before"),
                Part.from_tool_request(name="lookup", input={"id": 1}, ref="call-1"),
                Part.from_text("after"),
            ],
        )
    ]

    asyncio.run(ai.generate(model=model, messages=messages))

    assert client.responses.calls[0]["input"] == [
        {"role": "assistant", "content": "before"},
        {
            "type": "function_call",
            "call_id": "call-1",
            "name": "lookup",
            "arguments": '{"id":1}',
        },
        {"role": "assistant", "content": "after"},
    ]


def test_responses_adapter_rejects_tool_role_text_without_provider_call() -> None:
    ai = Genkit()
    client = _FakeClient()
    model = define_openai_responses_model(ai, client=client)

    response = asyncio.run(
        ai.generate(
            model=model,
            messages=[Message(role=Role.TOOL, content=[Part.from_text("not a tool result")])],
        )
    )

    assert response.error is not None
    assert response.error.status == "INVALID_ARGUMENT"
    assert client.responses.calls == []


def test_responses_adapter_maps_tool_choice_and_bounded_output_control() -> None:
    ai = Genkit()
    client = _FakeClient()
    model = define_openai_responses_model(ai, client=client)

    asyncio.run(
        ai.generate(
            model=model,
            prompt="bounded",
            tool_choice=ToolChoice.REQUIRED,
            config={"max_output_tokens": 123, "reasoning_effort": "high"},
        )
    )

    assert client.responses.calls[0]["tool_choice"] == "required"
    assert client.responses.calls[0]["max_output_tokens"] == 123
    assert client.responses.calls[0]["reasoning"] == {"effort": "high"}


def test_responses_adapter_rejects_unknown_config_and_unsupported_parts() -> None:
    ai = Genkit()
    client = _FakeClient()
    model = define_openai_responses_model(ai, client=client)

    unknown = asyncio.run(
        ai.generate(model=model, prompt="bad config", config={"temperature": 0.5})
    )
    unsupported = asyncio.run(
        ai.generate(
            model=model,
            messages=[Message(role=Role.USER, content=[Part(data={"hidden": True})])],
        )
    )

    assert unknown.error is not None
    assert unknown.error.status == "INVALID_ARGUMENT"
    assert unsupported.error is not None
    assert unsupported.error.status == "INVALID_ARGUMENT"
    assert client.responses.calls == []


def test_responses_adapter_preserves_open_map_tool_schema_and_disables_strict() -> None:
    from pydantic import BaseModel

    class MapInput(BaseModel):
        labels: dict[str, str]

    ai = Genkit()
    client = _FakeClient()
    model = define_openai_responses_model(ai, client=client)

    @ai.tool(name="double_value")
    async def map_tool(input: MapInput) -> int:
        return len(input.labels)

    asyncio.run(ai.generate(model=model, prompt="map", tools=[map_tool], max_turns=1))

    definition = client.responses.calls[0]["tools"][0]
    assert definition["strict"] is False
    assert definition["parameters"]["properties"]["labels"]["additionalProperties"] == {
        "type": "string"
    }


def test_responses_adapter_accepts_already_strict_nested_defs_without_rewriting() -> None:
    from pydantic import BaseModel, ConfigDict

    class Nested(BaseModel):
        model_config = ConfigDict(extra="forbid")

        value: str

    class NestedOutput(BaseModel):
        model_config = ConfigDict(extra="forbid")

        nested: Nested

    response_payload = SimpleNamespace(
        status="completed",
        output_text='{"nested":{"value":"ok"}}',
        output=[
            SimpleNamespace(
                type="message",
                content=[SimpleNamespace(type="output_text", text='{"nested":{"value":"ok"}}')],
            )
        ],
        usage=None,
    )
    ai = Genkit()
    client = _StaticClient(response_payload)
    model = define_openai_responses_model(ai, client=client)

    result = asyncio.run(
        ai.generate(
            model=model,
            prompt="nested",
            output_format="json",
            output_schema=NestedOutput,
        )
    )

    assert result.output == NestedOutput(nested=Nested(value="ok"))
    schema = client.responses.calls[0]["text"]["format"]["schema"]
    assert schema["additionalProperties"] is False
    assert schema["$defs"]["Nested"]["additionalProperties"] is False


def test_fastapi_adapter_executes_typed_flow(tmp_path: Path) -> None:
    ai, agent, _store = build_deterministic_runtime(
        skill_root=_install_generic_projection(tmp_path)
    )
    response = TestClient(build_fastapi_app(ai, agent)).post(
        "/api/compatibility_health", json={"data": "ready"}
    )
    assert response.status_code == 200
    assert response.json() == {"result": "compatible:ready"}


def test_responses_adapter_collapses_provider_errors() -> None:
    ai = Genkit()
    model = define_openai_responses_model(ai, client=_FailingClient())

    response = asyncio.run(ai.generate(model=model, prompt="fail"))

    assert response.error is not None
    assert response.error.message == "OpenAI Responses request failed"
    assert "provider-secret" not in response.model_dump_json()


def test_responses_adapter_maps_incomplete_and_failed_states_safely() -> None:
    content = SimpleNamespace(type="output_text", text="partial")
    incomplete_response = SimpleNamespace(
        status="incomplete",
        incomplete_details=SimpleNamespace(reason="max_output_tokens"),
        output=[SimpleNamespace(type="message", content=[content])],
        output_text="partial",
        usage=SimpleNamespace(
            input_tokens=5,
            output_tokens=6,
            total_tokens=11,
            input_tokens_details=None,
            output_tokens_details=None,
        ),
    )
    failed_response = SimpleNamespace(
        status="failed",
        error=SimpleNamespace(message="upstream-sensitive-detail"),
        output=[],
        output_text="",
    )

    incomplete_ai = Genkit()
    incomplete_model = define_openai_responses_model(
        incomplete_ai, client=_StaticClient(incomplete_response)
    )
    incomplete = asyncio.run(incomplete_ai.generate(model=incomplete_model, prompt="limit"))

    failed_ai = Genkit()
    failed_model = define_openai_responses_model(failed_ai, client=_StaticClient(failed_response))
    failed = asyncio.run(failed_ai.generate(model=failed_model, prompt="fail"))

    assert str(incomplete.finish_reason) == "length"
    assert incomplete.finish_message == "OpenAI response reached the configured output limit"
    assert incomplete.text == "partial"
    assert incomplete.usage is not None
    assert incomplete.usage.total_tokens == 11
    assert failed.error is not None
    assert failed.error.message == "OpenAI Responses request failed"
    assert "sensitive" not in failed.model_dump_json()


def test_responses_adapter_maps_refusal_and_fails_closed_on_unknown_content() -> None:
    refusal = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                phase="final_answer",
                content=[SimpleNamespace(type="refusal", refusal="I cannot help with that")],
            )
        ],
        output_text="",
        usage=None,
    )
    unknown = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                phase="final_answer",
                content=[SimpleNamespace(type="future_content", value="ignored")],
            )
        ],
        output_text="",
        usage=None,
    )
    refusal_ai = Genkit()
    refusal_model = define_openai_responses_model(refusal_ai, client=_StaticClient(refusal))
    refused = asyncio.run(refusal_ai.generate(model=refusal_model, prompt="refuse"))
    unknown_ai = Genkit()
    unknown_model = define_openai_responses_model(unknown_ai, client=_StaticClient(unknown))
    unsupported = asyncio.run(unknown_ai.generate(model=unknown_model, prompt="unknown"))

    assert str(refused.finish_reason) == "blocked"
    assert refused.finish_message == "OpenAI declined to provide the requested output"
    assert refused.text == "I cannot help with that"
    assert unsupported.error is not None
    assert unsupported.error.message == "OpenAI Responses returned unsupported message content"


def test_responses_adapter_preserves_two_phase_messages_across_serialized_replay() -> None:
    first_response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(
                type="message",
                phase="commentary",
                content=[SimpleNamespace(type="output_text", text="working")],
            ),
            SimpleNamespace(
                type="message",
                phase="final_answer",
                content=[SimpleNamespace(type="output_text", text="done")],
            ),
        ],
        output_text="workingdone",
        usage=None,
    )
    first_ai = Genkit()
    first_model = define_openai_responses_model(first_ai, client=_StaticClient(first_response))
    first = asyncio.run(first_ai.generate(model=first_model, prompt="two phases"))
    assert first.message is not None
    replayed = Message.model_validate_json(first.message.model_dump_json())
    assert [part.metadata for part in replayed.content] == [
        {PHASE_METADATA_KEY: "commentary"},
        {PHASE_METADATA_KEY: "final_answer"},
    ]

    second_ai = Genkit()
    second_client = _FakeClient()
    second_model = define_openai_responses_model(second_ai, client=second_client)
    asyncio.run(second_ai.generate(model=second_model, messages=[replayed]))

    assert second_client.responses.calls[0]["input"] == [
        {"role": "assistant", "content": "working", "phase": "commentary"},
        {"role": "assistant", "content": "done", "phase": "final_answer"},
    ]


def test_responses_adapter_preserves_reasoning_across_serialized_replay() -> None:
    reasoning_item = {
        "id": "rs_final",
        "summary": [{"type": "summary_text", "text": "checked the result"}],
        "type": "reasoning",
        "encrypted_content": "encrypted-final-reasoning",
        "status": "completed",
    }
    response = SimpleNamespace(
        status="completed",
        output=[
            SimpleNamespace(**reasoning_item),
            SimpleNamespace(
                type="message",
                phase="final_answer",
                content=[SimpleNamespace(type="output_text", text="done")],
            ),
        ],
        output_text="done",
        usage=None,
    )
    first_ai = Genkit()
    first_model = define_openai_responses_model(first_ai, client=_StaticClient(response))
    first = asyncio.run(first_ai.generate(model=first_model, prompt="reason"))
    assert first.message is not None
    replayed = Message.model_validate_json(first.message.model_dump_json())
    assert replayed.content[0].metadata == {REASONING_METADATA_KEY: reasoning_item}

    second_ai = Genkit()
    second_client = _FakeClient()
    second_model = define_openai_responses_model(second_ai, client=second_client)
    asyncio.run(second_ai.generate(model=second_model, messages=[replayed]))

    assert second_client.responses.calls[0]["input"][:2] == [
        reasoning_item,
        {"role": "assistant", "content": "done", "phase": "final_answer"},
    ]


def test_responses_adapter_rejects_forged_reasoning_item_type_before_provider() -> None:
    ai = Genkit()
    client = _FakeClient()
    model = define_openai_responses_model(ai, client=client)
    forged = Part(
        reasoning="not trusted",
        metadata={
            REASONING_METADATA_KEY: {
                "id": "item_1",
                "summary": [],
                "type": "function_call",
            }
        },
    )

    result = asyncio.run(
        ai.generate(model=model, messages=[Message(role=Role.MODEL, content=[forged])])
    )

    assert result.error is not None
    assert result.error.status == "INVALID_ARGUMENT"
    assert client.responses.calls == []


def test_responses_adapter_aborts_and_closes_stalled_stream() -> None:
    async def check() -> None:
        stream = _StalledStream()
        ai = Genkit()
        model = define_openai_responses_model(ai, client=_StalledClient(stream))
        agent = ai.define_agent(
            name="abortable_responses_agent",
            model=model,
            store=RecordingSessionStore(),
        )

        task = await agent.chat().detach("wait")
        await asyncio.wait_for(stream.started.wait(), timeout=1)
        assert str(await task.abort()) == "pending"
        snapshot = await asyncio.wait_for(task.wait(interval=0.01), timeout=1)
        assert str(snapshot.status) == "aborted"
        await asyncio.wait_for(stream.cancel_observed.wait(), timeout=1)
        await asyncio.wait_for(stream.close_observed.wait(), timeout=1)
        assert stream.cancelled is True
        assert stream.closed is True
        assert stream.close_count == 1

    asyncio.run(check())


def test_responses_adapter_aborts_stalled_nonstream_create() -> None:
    async def check() -> None:
        responses = _BlockedCreateResponses()
        ai = Genkit()
        model = define_openai_responses_model(ai, client=_BlockedCreateClient(responses))
        action = ai.registry.registered_action(ActionKind.MODEL, model)
        assert action is not None
        abort_signal = asyncio.Event()
        request: ModelRequest[Any] = ModelRequest(
            messages=[Message(role=Role.USER, content=[Part.from_text("wait")])]
        )
        run = asyncio.create_task(action.run(request, abort_signal=abort_signal))
        await asyncio.wait_for(responses.started.wait(), timeout=1)
        abort_signal.set()
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(run, timeout=1)
        await asyncio.wait_for(responses.cancelled.wait(), timeout=1)

    asyncio.run(check())


def test_responses_adapter_maps_stream_incomplete_and_closes_error_streams() -> None:
    async def check() -> None:
        content = SimpleNamespace(type="output_text", text="partial")
        incomplete_response = SimpleNamespace(
            status="incomplete",
            incomplete_details=SimpleNamespace(reason="max_output_tokens"),
            output=[SimpleNamespace(type="message", content=[content])],
            output_text="partial",
            usage=None,
        )
        incomplete_stream = _EventStream(
            [SimpleNamespace(type="response.incomplete", response=incomplete_response)]
        )
        incomplete_ai = Genkit()
        incomplete_model = define_openai_responses_model(
            incomplete_ai, client=_EventStreamClient(incomplete_stream)
        )
        turn = incomplete_ai.generate_stream(model=incomplete_model, prompt="limit")
        async for _chunk in turn.stream:
            pass
        incomplete = await turn.response

        error_stream = _EventStream([SimpleNamespace(type="error", message="secret")])
        error_ai = Genkit()
        error_model = define_openai_responses_model(
            error_ai, client=_EventStreamClient(error_stream)
        )
        failed_turn = error_ai.generate_stream(model=error_model, prompt="fail")
        async for _chunk in failed_turn.stream:
            pass
        failed = await failed_turn.response

        assert str(incomplete.finish_reason) == "length"
        assert incomplete_stream.closed is True
        assert failed.error is not None
        assert failed.error.message == "OpenAI Responses request failed"
        assert "secret" not in failed.model_dump_json()
        assert error_stream.closed is True

    asyncio.run(check())


def test_live_openai_probe_is_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("RUN_LIVE_GENKIT_OPENAI", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("TEAM_AGENT_OPENAI_MODEL", raising=False)
    with pytest.raises(CompatibilityError, match="opt in"):
        asyncio.run(run_live_openai_probe())
    monkeypatch.setenv("RUN_LIVE_GENKIT_OPENAI", "1")
    with pytest.raises(CompatibilityError, match="OPENAI_API_KEY"):
        asyncio.run(run_live_openai_probe())


def test_trace_commands_are_pinned_and_get_is_machine_readable() -> None:
    commands = trace_inspection_commands("trace-123")
    assert commands[0][-1] == "trace:list"
    assert commands[1][-4:] == ["trace:get", "trace-123", "--format", "json"]

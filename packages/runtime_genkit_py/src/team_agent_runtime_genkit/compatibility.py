"""Executable Genkit compatibility gate, separate from the production runtime."""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections.abc import AsyncIterator, Callable
from pathlib import Path
from typing import Any

from fastapi import FastAPI
from genkit import ActionRunContext, Role
from genkit.exp import Genkit
from genkit.exp.agent import InMemorySessionStore, SessionSnapshot
from genkit.model import Message, ModelRequest, ModelResponse, ModelResponseChunk, Part
from genkit_fastapi import serve_agent, serve_flow
from genkit_middleware import Middleware, Skills
from openai import AsyncOpenAI
from pydantic import BaseModel, ConfigDict, Field

from team_agent_runtime_genkit.openai_responses import define_openai_responses_model

GENKIT_CLI_VERSION = "1.43.0"
LIVE_CHECK_ENV = "RUN_LIVE_GENKIT_OPENAI"
MODEL_ENV = "TEAM_AGENT_OPENAI_MODEL"


class CompatibilityError(RuntimeError):
    """Safe configuration or compatibility error."""


class ArithmeticInput(BaseModel):
    model_config = ConfigDict(extra="forbid")

    value: int = Field(ge=0, le=1_000)


class StructuredResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    answer: int
    evidence: list[str]


class ProbeReport(BaseModel):
    model_config = ConfigDict(extra="forbid")

    runtime_path: str
    tool_result: str
    structured_answer: int
    streamed_text: str
    continuation_turns: int
    store_reads: int
    store_writes: int
    skill_loaded: bool
    abort_status: str
    fastapi_routes: list[str]
    trace_commands: list[list[str]]


class RecordingSessionStore:
    """Protocol-level store wrapper proving custom store hooks without persistence."""

    def __init__(self) -> None:
        self._delegate = InMemorySessionStore()
        self.reads = 0
        self.writes = 0

    async def get_snapshot(
        self,
        *,
        snapshot_id: str | None = None,
        session_id: str | None = None,
        context: dict[str, Any] | None = None,
    ) -> SessionSnapshot | None:
        self.reads += 1
        return await self._delegate.get_snapshot(
            snapshot_id=snapshot_id,
            session_id=session_id,
            context=context,
        )

    async def save_snapshot(
        self,
        snapshot_id: str,
        fn: Callable[[SessionSnapshot | None], SessionSnapshot | None],
        *,
        context: dict[str, Any] | None = None,
    ) -> SessionSnapshot | None:
        self.writes += 1
        return await self._delegate.save_snapshot(snapshot_id, fn, context=context)

    async def on_snapshot_status_change(
        self,
        snapshot_id: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> AsyncIterator[Any]:
        return await self._delegate.on_snapshot_status_change(snapshot_id, context=context)


def trace_inspection_commands(trace_id: str = "<trace_id>") -> list[list[str]]:
    """Return pinned, non-shell trace inspection commands."""

    return [
        ["npx", "--yes", f"genkit-cli@{GENKIT_CLI_VERSION}", "trace:list"],
        [
            "npx",
            "--yes",
            f"genkit-cli@{GENKIT_CLI_VERSION}",
            "trace:get",
            trace_id,
            "--format",
            "json",
        ],
    ]


def _last_user_text(request: ModelRequest[Any]) -> str:
    for message in reversed(request.messages):
        if message.role == Role.USER:
            return "".join(part.text or "" for part in message.content)
    return ""


def _has_tool_response(request: ModelRequest[Any], name: str) -> bool:
    return any(
        part.tool_response is not None and part.tool_response.name == name
        for message in request.messages
        for part in message.content
    )


def _model_message(*parts: Part) -> ModelResponse:
    return ModelResponse(message=Message(role=Role.MODEL, content=list(parts)))


def build_deterministic_runtime(
    *, skill_root: Path, slow_started: asyncio.Event | None = None
) -> tuple[Genkit, Any, RecordingSessionStore]:
    """Build a credential-free agent exercising the selected experimental agent path."""

    ai = Genkit(plugins=[Middleware()])
    store = RecordingSessionStore()

    class _State:
        requests = 0

    state = _State()

    async def deterministic_model(
        request: ModelRequest[Any], context: ActionRunContext[Any]
    ) -> ModelResponse:
        state.requests += 1
        prompt = _last_user_text(request)
        tool_names = {tool.name for tool in request.tools or []}

        if "slow" in prompt:
            if slow_started is not None:
                slow_started.set()
            await asyncio.sleep(10)
            return _model_message(Part.from_text("slow complete"))

        if "skill" in prompt and "use_skill" in tool_names:
            if not _has_tool_response(request, "use_skill"):
                return _model_message(
                    Part.from_tool_request(
                        name="use_skill", input={"skill_name": "compat-probe"}, ref="skill-1"
                    )
                )
            return _model_message(Part.from_text("loaded COMPATIBILITY_PROJECTION_MARKER"))

        if "double" in prompt and "double_value" in tool_names:
            if not _has_tool_response(request, "double_value"):
                return _model_message(
                    Part.from_tool_request(name="double_value", input={"value": 21}, ref="double-1")
                )
            return _model_message(Part.from_text("42"))

        if request.output_format:
            return _model_message(
                Part.from_text(json.dumps({"answer": 42, "evidence": ["deterministic-model"]}))
            )

        if "stream" in prompt:
            for index, text in enumerate(("stream", "-", "ok")):
                context.send_chunk(
                    ModelResponseChunk(role=Role.MODEL, index=index, content=[Part.from_text(text)])
                )
            return _model_message(Part.from_text("stream-ok"))

        user_turns = sum(message.role == Role.USER for message in request.messages)
        return _model_message(Part.from_text(f"turns={user_turns}"))

    ai.define_model(name="compatibility_model", fn=deterministic_model)

    @ai.tool(name="double_value", description="Double a bounded integer.")
    async def double_value(input: ArithmeticInput) -> int:
        return input.value * 2

    agent = ai.define_agent(
        name="compatibility_agent",
        model="compatibility_model",
        system="Compatibility probe. Follow the requested deterministic operation.",
        tools=[double_value],
        use=[Skills(skill_paths=[str(skill_root)])],
        store=store,
        max_turns=5,
    )
    return ai, agent, store


def build_fastapi_app(ai: Genkit, agent: Any) -> FastAPI:
    """Mount both the selected agent surface and a typed flow surface."""

    @ai.flow(name="compatibility_health")
    async def compatibility_health(value: str) -> str:
        return f"compatible:{value}"

    app = FastAPI(title="Genkit compatibility probe")
    app.include_router(serve_agent(agent), prefix="/api")
    app.include_router(serve_flow(compatibility_health), prefix="/api")
    return app


async def run_deterministic_probe(skill_root: Path) -> ProbeReport:
    """Execute the credential-free compatibility gate."""

    if not skill_root.is_dir():
        raise CompatibilityError("skill_root must be an installed generic skill projection")

    slow_started = asyncio.Event()
    ai, agent, store = build_deterministic_runtime(skill_root=skill_root, slow_started=slow_started)

    tool_response = await agent.chat().send("double")

    structured = await ai.generate(
        model="compatibility_model",
        prompt="structured",
        output_format="json",
        output_schema=StructuredResult,
    )
    if not isinstance(structured.output, StructuredResult):
        raise CompatibilityError("Genkit did not parse the structured response")

    stream_turn = agent.chat().send_stream("stream")
    streamed: list[str] = []
    async for chunk in stream_turn.stream:
        if chunk.text:
            streamed.append(chunk.text)
    await stream_turn.response

    continued = agent.chat()
    first = await continued.send("first")
    resumed = await agent.load_chat(snapshot_id=first.snapshot_id)
    second = await resumed.send("second")

    skill_response = await agent.chat().send("skill")

    abort_chat = agent.chat()
    task = await abort_chat.detach("slow")
    await asyncio.wait_for(slow_started.wait(), timeout=1)
    previous_status = await task.abort()
    snapshot = await task.wait(interval=0.01)

    app = build_fastapi_app(ai, agent)
    routes = sorted(app.openapi()["paths"])

    return ProbeReport(
        runtime_path="genkit.exp.Genkit.define_agent",
        tool_result=tool_response.text,
        structured_answer=structured.output.answer,
        streamed_text="".join(streamed),
        continuation_turns=int(second.text.removeprefix("turns=")),
        store_reads=store.reads,
        store_writes=store.writes,
        skill_loaded="COMPATIBILITY_PROJECTION_MARKER" in skill_response.text,
        abort_status=f"{previous_status}->{snapshot.status}",
        fastapi_routes=routes,
        trace_commands=trace_inspection_commands(),
    )


async def run_live_openai_probe() -> str:
    """Run one bounded live call only when explicitly opted in and configured."""

    if os.environ.get(LIVE_CHECK_ENV) != "1":
        raise CompatibilityError(f"set {LIVE_CHECK_ENV}=1 to opt in to the live provider check")
    api_key = os.environ.get("OPENAI_API_KEY")
    model_id = os.environ.get(MODEL_ENV)
    if not api_key or not model_id:
        raise CompatibilityError(f"OPENAI_API_KEY and {MODEL_ENV} are required")
    ai = Genkit()
    model = define_openai_responses_model(
        ai, model_id=model_id, client=AsyncOpenAI(api_key=api_key)
    )
    response = await ai.generate(model=model, prompt='Reply with exactly "compatible".')
    return response.text


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the S07 Genkit compatibility gate")
    parser.add_argument("--skill-root", type=Path)
    parser.add_argument("--live-openai", action="store_true")
    return parser


def main() -> None:
    args = _parser().parse_args()
    if args.live_openai:
        print(json.dumps({"live_openai_text": asyncio.run(run_live_openai_probe())}))
        return
    if args.skill_root is None:
        raise SystemExit("--skill-root is required for the deterministic probe")
    report = asyncio.run(run_deterministic_probe(args.skill_root))
    print(report.model_dump_json())

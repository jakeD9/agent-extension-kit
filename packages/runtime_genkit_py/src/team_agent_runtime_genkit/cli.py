"""Bounded JSON CLI for a single stateless coordinator invocation."""

from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import BinaryIO, TextIO

from pydantic import ValidationError
from team_agent_runtime import AgentRuntime, AgentTurnRequest, AgentTurnStatus

from team_agent_runtime_genkit.coordinator import (
    CoordinatorConfigurationError,
    DeterministicCoordinatorModelFactory,
    GenkitCoordinatorRuntime,
    ModelFactory,
    OpenAIResponsesModelFactory,
)
from team_agent_runtime_genkit.openai_responses import DEFAULT_MODEL_ID

MAX_REQUEST_BYTES = 256 * 1024
RuntimeFactory = Callable[[], AgentRuntime]


def _error(stderr: TextIO, code: str, message: str) -> int:
    stderr.write(json.dumps({"error": {"code": code, "message": message}}, separators=(",", ":")))
    stderr.write("\n")
    return 2


def _default_runtime() -> AgentRuntime:
    context_url = os.environ.get("TEAM_AGENT_CONTEXT_URL", "")
    token = os.environ.get("TEAM_AGENT_TOKEN", "")
    if not context_url or not token:
        raise CoordinatorConfigurationError(
            "TEAM_AGENT_CONTEXT_URL and TEAM_AGENT_TOKEN are required"
        )
    mode = os.environ.get("TEAM_AGENT_MODEL_MODE", "openai")
    if mode == "deterministic":
        model_factory: ModelFactory = DeterministicCoordinatorModelFactory()
    elif mode == "openai":
        model_factory = OpenAIResponsesModelFactory(
            api_key=os.environ.get("OPENAI_API_KEY", ""),
            model_id=os.environ.get("TEAM_AGENT_OPENAI_MODEL", DEFAULT_MODEL_ID),
        )
    else:
        raise CoordinatorConfigurationError("TEAM_AGENT_MODEL_MODE must be deterministic or openai")
    return GenkitCoordinatorRuntime(
        context_url=context_url,
        token=token,
        model_factory=model_factory,
        projection_parent=Path(tempfile.gettempdir()),
    )


async def run_cli(
    argv: Sequence[str],
    *,
    stdin: BinaryIO,
    stdout: TextIO,
    stderr: TextIO,
    runtime_factory: RuntimeFactory = _default_runtime,
) -> int:
    if list(argv) != ["invoke"]:
        return _error(stderr, "invalid_arguments", "Usage: team-agent-runtime invoke")

    try:
        payload = stdin.read(MAX_REQUEST_BYTES + 1)
    except OSError:
        return _error(stderr, "request_read_failed", "Request could not be read.")
    if len(payload) > MAX_REQUEST_BYTES:
        return _error(stderr, "request_too_large", "Request exceeds the size limit.")
    try:
        request = AgentTurnRequest.model_validate_json(payload)
    except ValidationError:
        return _error(stderr, "invalid_request", "Request JSON is invalid.")
    if request.conversation_context is not None or request.job_completion_context is not None:
        return _error(
            stderr,
            "invalid_request",
            "Durable conversation and job context cannot be supplied to the stateless CLI.",
        )
    try:
        runtime = runtime_factory()
    except CoordinatorConfigurationError:
        return _error(stderr, "configuration_error", "Runtime configuration is invalid.")

    try:
        result = await runtime.execute(request)
    except Exception:
        return _error(stderr, "runtime_error", "Runtime invocation failed.")
    stdout.write(result.model_dump_json())
    stdout.write("\n")
    return 0 if result.status == AgentTurnStatus.COMPLETED else 1


def main() -> None:
    code = asyncio.run(
        run_cli(
            sys.argv[1:],
            stdin=sys.stdin.buffer,
            stdout=sys.stdout,
            stderr=sys.stderr,
        )
    )
    raise SystemExit(code)


__all__ = ["MAX_REQUEST_BYTES", "main", "run_cli"]

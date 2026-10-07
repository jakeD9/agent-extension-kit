"""Pinned noninteractive Codex CLI request and result boundary."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from team_agent_runtime.jobs import CodingJobOutcome, ReusableLesson


class CodexHarnessError(RuntimeError):
    """Codex emitted malformed or oversized structured output."""


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class CodexFinalOutput(_Contract):
    schema_version: Literal["1"]
    outcome: CodingJobOutcome
    summary: str = Field(min_length=1, max_length=20_000)
    reusable_lesson: ReusableLesson | None


def codex_output_schema() -> dict[str, object]:
    """Return the strict schema supplied directly to ``codex exec``."""
    return CodexFinalOutput.model_json_schema()


def codex_exec_argv(
    *,
    executable: str = "codex",
    schema_path: str = "/run/team-agent/output-schema.json",
    output_path: str = "/artifacts/final-output.json",
    workspace: str = "/workspace",
) -> list[str]:
    """Build an argv-only invocation; no shell or repository-provided config is loaded."""
    return [
        executable,
        "exec",
        "--ephemeral",
        "--ignore-user-config",
        "--strict-config",
        "--sandbox",
        "workspace-write",
        "--approve-for-me",
        "--json",
        "--color",
        "never",
        "--output-schema",
        schema_path,
        "--output-last-message",
        output_path,
        "--cd",
        workspace,
        "-",
    ]


def parse_codex_jsonl(payload: bytes, *, max_bytes: int) -> list[dict[str, object]]:
    if len(payload) > max_bytes:
        raise CodexHarnessError("Codex JSONL exceeded the output limit")
    events: list[dict[str, object]] = []
    for number, line in enumerate(payload.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            value = json.loads(line)
        except json.JSONDecodeError as error:
            raise CodexHarnessError(f"Codex JSONL line {number} is invalid") from error
        if not isinstance(value, dict):
            raise CodexHarnessError(f"Codex JSONL line {number} must be an object")
        events.append(value)
        if len(events) > 20_000:
            raise CodexHarnessError("Codex emitted too many events")
    return events


def read_codex_final(path: Path, *, max_bytes: int) -> CodexFinalOutput:
    if path.is_symlink() or not path.is_file():
        raise CodexHarnessError("Codex did not emit its final structured output")
    size = path.stat().st_size
    if size > max_bytes:
        raise CodexHarnessError("Codex final output exceeded the output limit")
    try:
        return CodexFinalOutput.model_validate_json(path.read_bytes())
    except (OSError, ValueError) as error:
        raise CodexHarnessError("Codex final output is invalid") from error


__all__ = [
    "CodexFinalOutput",
    "CodexHarnessError",
    "codex_exec_argv",
    "codex_output_schema",
    "parse_codex_jsonl",
    "read_codex_final",
]

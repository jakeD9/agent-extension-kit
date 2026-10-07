"""Provider-neutral contracts for supervised disposable coding environments."""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class _Contract(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ExecutionPhase(StrEnum):
    PREPARING = "preparing"
    RUNNING = "running"
    VERIFYING = "verifying"
    PERSISTING = "persisting"


class ExecutionPolicy(_Contract):
    """Bounded capabilities applied by the supervisor, never by model output."""

    schema_version: Literal["1"] = "1"
    network: Literal["none", "model_only"] = "none"
    timeout_seconds: int = Field(default=900, ge=1, le=7_200)
    memory_megabytes: int = Field(default=2_048, ge=256, le=16_384)
    cpu_count: float = Field(default=2.0, gt=0, le=16)
    pids_limit: int = Field(default=256, ge=32, le=4_096)
    max_patch_bytes: int = Field(default=1_000_000, ge=1, le=8_000_000)
    max_output_bytes: int = Field(default=2_000_000, ge=1, le=8_000_000)
    editable_paths: list[str] = Field(default_factory=list, max_length=100)
    checks: list[list[str]] = Field(default_factory=list, max_length=20)

    @model_validator(mode="after")
    def bounded_argv_checks(self) -> ExecutionPolicy:
        if any(
            not argv
            or len(argv) > 32
            or any(not value or len(value) > 2_000 or "\x00" in value for value in argv)
            for argv in self.checks
        ):
            raise ValueError("checks must be bounded non-empty argv arrays")
        if any(not _safe_prefix(value) for value in self.editable_paths):
            raise ValueError("editable_paths must be safe repository-relative prefixes")
        if len(set(self.editable_paths)) != len(self.editable_paths):
            raise ValueError("editable_paths must be unique")
        return self


def _safe_prefix(value: str) -> bool:
    path = PurePosixPath(value)
    return bool(
        value
        and len(value) <= 2_000
        and "\\" not in value
        and "\x00" not in value
        and not path.is_absolute()
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


class ExecutionProgress(_Contract):
    schema_version: Literal["1"] = "1"
    phase: ExecutionPhase
    detail: str = Field(min_length=1, max_length=500)


class ExecutionArtifact(_Contract):
    """Supervisor-owned metadata for one bounded artifact on durable storage."""

    schema_version: Literal["1"] = "1"
    name: Literal["patch", "codex_jsonl", "final_output", "verification"]
    path: str = Field(min_length=1, max_length=2_000)
    sha256: str = Field(pattern=r"^sha256:[0-9a-f]{64}$")
    size: int = Field(ge=0, le=8_000_000)

    @model_validator(mode="after")
    def repository_relative_path(self) -> ExecutionArtifact:
        candidate = PurePosixPath(self.path)
        if (
            candidate.is_absolute()
            or "\\" in self.path
            or "\x00" in self.path
            or any(part in {"", ".", ".."} for part in candidate.parts)
        ):
            raise ValueError("artifact paths must be safe relative paths")
        return self


class ExecutionArtifacts(_Contract):
    schema_version: Literal["1"] = "1"
    root: str = Field(min_length=1, max_length=2_000)
    items: list[ExecutionArtifact] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def unique_artifact_names(self) -> ExecutionArtifacts:
        if len({item.name for item in self.items}) != len(self.items):
            raise ValueError("artifact names must be unique")
        return self

    def local_root(self) -> Path:
        return Path(self.root)


class VerificationReport(_Contract):
    """Credential-free verifier observations trusted by the supervisor."""

    schema_version: Literal["1"]
    passed: bool
    changed_paths: list[str] = Field(max_length=2_000)
    checks: list[str] = Field(max_length=24)

    @model_validator(mode="after")
    def safe_paths_and_checks(self) -> VerificationReport:
        if len(set(self.changed_paths)) != len(self.changed_paths):
            raise ValueError("changed_paths must be unique")
        if any(not _safe_prefix(path) for path in self.changed_paths):
            raise ValueError("changed_paths must be safe repository-relative paths")
        if any(not check or len(check) > 2_000 for check in self.checks):
            raise ValueError("checks must be bounded")
        return self


__all__ = [
    "ExecutionArtifact",
    "ExecutionArtifacts",
    "ExecutionPhase",
    "ExecutionPolicy",
    "ExecutionProgress",
    "VerificationReport",
]

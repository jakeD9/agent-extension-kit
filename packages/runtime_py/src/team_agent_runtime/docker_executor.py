"""Local Docker supervisor for pinned, disposable Codex coding jobs."""

from __future__ import annotations

import asyncio
import json
import os
import re
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Literal, Protocol

from pydantic import ValidationError
from team_agent_contracts import SkillLock
from team_agent_skills import SkillClient, SkillDistributionError, SkillInstaller

from team_agent_runtime.coding_runner import RunnerRequest, RunnerResult
from team_agent_runtime.execution import (
    ExecutionArtifact,
    ExecutionArtifacts,
    ExecutionPhase,
    ExecutionProgress,
    VerificationReport,
)
from team_agent_runtime.jobs import (
    DEFAULT_JOB_LEASE_TTL,
    CodingJobClaim,
    CodingJobFailure,
    CodingJobOutcome,
    CodingJobRepository,
    CodingJobResult,
    CodingJobStatus,
    CodingRunSnapshot,
    ReusableLesson,
    StaleCodingJobClaimError,
    coding_job_request_fingerprint,
)

_REVISION = re.compile(r"^[0-9a-f]{40}(?:[0-9a-f]{24})?$")
_SAFE_ID = re.compile(r"[^a-zA-Z0-9_.-]+")


class ExecutionStartupError(RuntimeError):
    """Pinned input could not be prepared safely; retrying will not change it."""


class DockerExecutionError(RuntimeError):
    """Disposable environment failed before returning a valid result."""


@dataclass(frozen=True)
class CommandResult:
    exit_code: int
    stdout: bytes = b""
    stderr: bytes = b""


class CommandAdapter(Protocol):
    async def run(
        self, argv: Sequence[str], *, timeout_seconds: int | None = None
    ) -> CommandResult: ...


class SubprocessCommandAdapter:
    """Small argv-only process adapter; it never invokes a shell."""

    def __init__(self, *, max_output_bytes: int = 2_000_000) -> None:
        self._max_output_bytes = max_output_bytes

    async def run(
        self, argv: Sequence[str], *, timeout_seconds: int | None = None
    ) -> CommandResult:
        if not argv or any("\x00" in item for item in argv):
            raise ValueError("command argv is invalid")
        process = await asyncio.create_subprocess_exec(
            *argv,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        async def bounded_read(reader: asyncio.StreamReader | None) -> bytes:
            if reader is None:
                return b""
            chunks: list[bytes] = []
            size = 0
            while chunk := await reader.read(64 * 1024):
                size += len(chunk)
                if size > self._max_output_bytes:
                    process.kill()
                    raise DockerExecutionError("command output exceeded the supervisor limit")
                chunks.append(chunk)
            return b"".join(chunks)

        try:
            stdout, stderr, _ = await asyncio.wait_for(
                asyncio.gather(
                    bounded_read(process.stdout),
                    bounded_read(process.stderr),
                    process.wait(),
                ),
                timeout=timeout_seconds,
            )
        except asyncio.CancelledError:
            process.terminate()
            try:
                await asyncio.wait_for(process.wait(), timeout=2)
            except TimeoutError:
                process.kill()
                await process.wait()
            raise
        except TimeoutError:
            process.kill()
            await process.wait()
            raise
        if len(stdout) + len(stderr) > self._max_output_bytes:
            raise DockerExecutionError("command output exceeded the supervisor limit")
        return CommandResult(process.returncode or 0, stdout, stderr)


class LocalRepositoryCatalog:
    """Allowlist repository identifiers to administrator-configured local Git paths."""

    def __init__(self, sources: Mapping[str, Path], commands: CommandAdapter) -> None:
        self._sources = dict(sources)
        self._commands = commands

    async def bundle(self, repository: str, revision: str, destination: Path) -> None:
        source = self._sources.get(repository)
        if source is None:
            raise ExecutionStartupError("repository is not configured for local execution")
        if not _REVISION.fullmatch(revision):
            raise ExecutionStartupError("repository_revision must be a full commit object ID")
        if source.is_symlink() or not source.is_dir():
            raise ExecutionStartupError("configured repository source is unavailable or unsafe")
        resolved = await self._commands.run(
            ["git", "-C", str(source), "rev-parse", "--verify", f"{revision}^{{commit}}"]
        )
        if resolved.exit_code != 0 or resolved.stdout.decode().strip() != revision:
            raise ExecutionStartupError("pinned repository revision is unavailable")
        staging = destination.parent / ".bundle-repository"
        temporary = destination.parent / ".repository.bundle.tmp"
        if staging.exists():
            if staging.is_symlink() or not staging.is_dir():
                raise ExecutionStartupError("unsafe repository staging path")
            shutil.rmtree(staging)
        if temporary.exists():
            if temporary.is_symlink() or not temporary.is_file():
                raise ExecutionStartupError("unsafe repository bundle staging path")
            temporary.unlink()
        try:
            cloned = await self._commands.run(
                ["git", "clone", "--bare", "--no-hardlinks", str(source), str(staging)]
            )
            if cloned.exit_code != 0:
                raise ExecutionStartupError("could not stage the configured repository")
            referenced = await self._commands.run(
                [
                    "git",
                    "-C",
                    str(staging),
                    "update-ref",
                    "refs/heads/team-agent-pinned",
                    revision,
                ]
            )
            created = await self._commands.run(
                [
                    "git",
                    "-C",
                    str(staging),
                    "bundle",
                    "create",
                    str(temporary),
                    "refs/heads/team-agent-pinned",
                ]
            )
            if referenced.exit_code != 0 or created.exit_code != 0 or not temporary.is_file():
                raise ExecutionStartupError("could not create the pinned repository bundle")
            await self.validate_bundle(revision, temporary)
            os.replace(temporary, destination)
        finally:
            shutil.rmtree(staging, ignore_errors=True)
            temporary.unlink(missing_ok=True)

    async def validate_bundle(self, revision: str, bundle: Path) -> None:
        if bundle.is_symlink() or not bundle.is_file():
            raise ExecutionStartupError("pinned repository bundle is unavailable or unsafe")
        listed = await self._commands.run(["git", "bundle", "list-heads", str(bundle)])
        expected = f"{revision} refs/heads/team-agent-pinned"
        if listed.exit_code != 0 or listed.stdout.decode().strip() != expected:
            raise ExecutionStartupError("pinned repository bundle has the wrong revision")


class FrozenCodexSkillStager:
    """Retarget the generic immutable lock and install the exact packages for Codex."""

    def __init__(self, client: SkillClient) -> None:
        self._client = client

    def stage(self, generic_lock: SkillLock, root: Path) -> Path:
        try:
            root.mkdir(parents=True, exist_ok=False)
            codex_lock = generic_lock.model_copy(update={"target": "codex"})
            lock_path = root / "codex-skills.lock.json"
            lock_path.write_text(codex_lock.model_dump_json(indent=2) + "\n")
            destination = root / "project" / ".agents" / "skills"
            cache = root / "project" / ".team-agent" / "skill-cache"
            SkillInstaller(
                destination,
                cache,
                project_root=root / "project",
            ).pull(
                self._client,
                project=generic_lock.project,
                lock_path=lock_path,
                target="codex",
                frozen=True,
            )
        except (OSError, SkillDistributionError) as error:
            raise ExecutionStartupError("locked Codex skills could not be staged") from error
        return destination


class SkillStager(Protocol):
    def stage(self, generic_lock: SkillLock, root: Path) -> Path: ...


@dataclass(frozen=True)
class DockerExecution:
    result: CodingJobResult
    artifacts: ExecutionArtifacts
    container_name: str


class LocalDockerRunner:
    """Create/reconcile one labelled runner and a separate credential-free verifier."""

    def __init__(
        self,
        commands: CommandAdapter,
        repositories: LocalRepositoryCatalog,
        skill_stager: SkillStager,
        *,
        artifact_root: Path,
        image: str,
        codex_home: Path | None = None,
        model_network: str | None = None,
        fake_codex_executable: Path | None = None,
    ) -> None:
        if not image:
            raise ValueError("coding-runner image is required")
        self._commands = commands
        self._repositories = repositories
        self._skill_stager = skill_stager
        self._artifact_root = artifact_root
        self._image = image
        self._codex_home = codex_home
        self._model_network = model_network
        self._fake_codex_executable = fake_codex_executable
        if codex_home is not None and (
            not codex_home.is_absolute() or codex_home.is_symlink() or not codex_home.is_dir()
        ):
            raise ValueError("Codex home must be an existing absolute non-symlinked directory")

    async def execute(
        self,
        claim: CodingJobClaim,
        *,
        verifying: Callable[[], Awaitable[None]] | None = None,
    ) -> DockerExecution:
        request = claim.request
        if request.harness != "codex":
            raise ExecutionStartupError("LocalDockerRunner accepts only codex jobs")
        if request.execution_policy.network == "model_only" and (
            self._codex_home is None or self._model_network is None
        ):
            raise ExecutionStartupError(
                "live Codex requires an explicit auth directory and controlled model network"
            )
        if self._fake_codex_executable is not None and request.execution_policy.network != "none":
            raise ExecutionStartupError("fake Codex is only permitted with networking disabled")
        attempt_root = self._attempt_root(claim)
        input_root = attempt_root / "input"
        runner_root = attempt_root / "runner-output"
        verifier_root = attempt_root / "verifier-output"
        durable_root = attempt_root / "artifacts"
        input_root.mkdir(parents=True, exist_ok=True)
        runner_root.mkdir(parents=True, exist_ok=True)
        verifier_root.mkdir(parents=True, exist_ok=True)
        durable_root.mkdir(parents=True, exist_ok=True)
        runner_root.chmod(0o733)
        verifier_root.chmod(0o733)
        bundle = input_root / "repository.bundle"
        if bundle.exists():
            await self._repositories.validate_bundle(request.repository_revision, bundle)
        else:
            await self._repositories.bundle(request.repository, request.repository_revision, bundle)
        skill_root = input_root / "skills"
        if skill_root.exists():
            self._validate_skill_projection(skill_root, request.selected_skill_lock)
        else:
            stage_root = attempt_root / "skill-stage"
            if stage_root.exists():
                if stage_root.is_symlink() or not stage_root.is_dir():
                    raise ExecutionStartupError("unsafe skill staging path")
                shutil.rmtree(stage_root)
            staged = await asyncio.to_thread(
                self._skill_stager.stage, request.selected_skill_lock, stage_root
            )
            temporary_skills = input_root / ".skills.tmp"
            if temporary_skills.exists():
                if temporary_skills.is_symlink() or not temporary_skills.is_dir():
                    raise ExecutionStartupError("unsafe skill projection staging path")
                shutil.rmtree(temporary_skills)
            shutil.copytree(staged, temporary_skills)
            self._validate_skill_projection(temporary_skills, request.selected_skill_lock)
            os.replace(temporary_skills, skill_root)
        (input_root / "request.json").write_text(
            RunnerRequest(
                schema_version="1", attempt=claim.attempt, request=request
            ).model_dump_json()
        )

        name = self._container_name(claim)
        labels = {
            "team-agent.managed": "true",
            "team-agent.namespace": "coding-runner",
            "team-agent.job-id": claim.job_id,
            "team-agent.attempt": str(claim.attempt),
            "team-agent.request-fingerprint": self._request_fingerprint(claim),
        }
        await self._cleanup_stale_attempts(claim)
        status = await self._inspect(name, labels)
        try:
            if status is None:
                self._clear_outputs(runner_root, verifier_root, durable_root)
                created = await self._commands.run(
                    self._create_argv(name, labels, claim, input_root, runner_root)
                )
                if created.exit_code != 0:
                    reconciled = await self._inspect(name, labels)
                    if reconciled is None:
                        raise DockerExecutionError("Docker could not create the coding runner")
                    status = reconciled
                else:
                    status = "created"
            if (
                status in {"created", "exited"}
                and not (runner_root / "runner-result.json").exists()
            ):
                if status == "exited":
                    raise DockerExecutionError("reconciled runner exited without a result")
                run = await self._commands.run(
                    ["docker", "start", "--attach", name],
                    timeout_seconds=request.execution_policy.timeout_seconds,
                )
                if run.exit_code != 0:
                    raise DockerExecutionError("Codex runner exited unsuccessfully")
            elif status == "running":
                waited = await self._commands.run(
                    ["docker", "wait", name],
                    timeout_seconds=request.execution_policy.timeout_seconds,
                )
                if waited.exit_code != 0 or waited.stdout.strip() != b"0":
                    raise DockerExecutionError("reconciled Codex runner exited unsuccessfully")
            runner_result = RunnerResult.model_validate_json(
                self._bounded_read(
                    runner_root / "runner-result.json",
                    request.execution_policy.max_output_bytes,
                )
            )
            self._remove_output(verifier_root / "verification.json")
            if verifying is not None:
                await verifying()
            report = await self._verify(claim, input_root, runner_root, verifier_root)
            verified = report.passed
            outcome = CodingJobOutcome(runner_result.outcome)
            summary = runner_result.summary
            outside_policy = self._outside_editable_paths(
                report.changed_paths, request.execution_policy.editable_paths
            )
            if outside_policy:
                outcome = CodingJobOutcome.UNSAFE_TO_PROCEED
                summary = "The generated patch modified paths outside the configured boundary."
            if not verified and outcome == CodingJobOutcome.FIXED:
                outcome = CodingJobOutcome.UNSAFE_TO_PROCEED
                summary = "The independent credential-free verification did not pass."
            artifacts = self._persist_artifacts(
                claim, runner_root, verifier_root / "verification.json", durable_root
            )
            lesson = (
                ReusableLesson.model_validate(runner_result.reusable_lesson)
                if runner_result.reusable_lesson is not None and verified
                else None
            )
            result = CodingJobResult(
                job_id=claim.job_id,
                outcome=outcome,
                summary=summary,
                changed_paths=report.changed_paths,
                checks=report.checks,
                reusable_lesson=lesson,
                artifacts=artifacts,
            )
            return DockerExecution(result=result, artifacts=artifacts, container_name=name)
        except BaseException as error:
            await self.cleanup(name)
            if isinstance(error, (OSError, ValidationError, ValueError)):
                raise DockerExecutionError("runner artifacts or output were invalid") from error
            raise

    async def cleanup(self, container_name: str) -> None:
        removed = await self._commands.run(["docker", "rm", "--force", container_name])
        if removed.exit_code != 0:
            message = removed.stderr.decode("utf-8", errors="replace")
            lowered = message.lower()
            if "no such container" not in lowered and "no such object" not in lowered:
                raise DockerExecutionError("Docker could not remove a coding runner")

    def container_name(self, claim: CodingJobClaim) -> str:
        return self._container_name(claim)

    def _attempt_root(self, claim: CodingJobClaim) -> Path:
        prefix = _SAFE_ID.sub("-", claim.job_id)[:60]
        digest = sha256(claim.job_id.encode()).hexdigest()[:16]
        safe_job = f"{prefix}-{digest}"
        root = self._artifact_root / safe_job / f"attempt-{claim.attempt}"
        if root.is_symlink():
            raise ExecutionStartupError("artifact path must not be a symbolic link")
        return root

    @staticmethod
    def _validate_skill_projection(root: Path, generic_lock: SkillLock) -> None:
        if root.is_symlink() or not root.is_dir():
            raise ExecutionStartupError("staged skill projection is unavailable or unsafe")
        lock_path = root / "skills.lock.json"
        if (
            lock_path.is_symlink()
            or not lock_path.is_file()
            or lock_path.stat().st_size > 1_000_000
        ):
            raise ExecutionStartupError("staged skill lock is unavailable or unsafe")
        try:
            installed_lock = SkillLock.model_validate_json(lock_path.read_bytes())
        except (OSError, ValidationError) as error:
            raise ExecutionStartupError("staged skill lock is invalid") from error
        expected_lock = generic_lock.model_copy(update={"target": "codex"})
        if installed_lock != expected_lock:
            raise ExecutionStartupError("staged skill lock does not match the job lock")
        expected_roots = {package.name for package in expected_lock.packages}
        allowed_roots = expected_roots | {"skills.lock.json", ".team-agent-ownership.json"}
        if {path.name for path in root.iterdir()} != allowed_roots:
            raise ExecutionStartupError("staged skill projection contains unexpected entries")
        for package in expected_lock.packages:
            package_root = root / package.name
            expected_files = {item.path: item for item in package.files}
            actual_files: set[str] = set()
            if package_root.is_symlink() or not package_root.is_dir():
                raise ExecutionStartupError("staged skill package is unavailable or unsafe")
            for path in package_root.rglob("*"):
                if path.is_symlink():
                    raise ExecutionStartupError("staged skill package contains a symbolic link")
                if path.is_dir():
                    continue
                relative = path.relative_to(package_root).as_posix()
                expected = expected_files.get(relative)
                if expected is None or path.stat().st_size != expected.size:
                    raise ExecutionStartupError("staged skill file inventory does not match")
                if sha256(path.read_bytes()).hexdigest() != expected.sha256:
                    raise ExecutionStartupError("staged skill file hash does not match")
                actual_files.add(relative)
            if actual_files != set(expected_files):
                raise ExecutionStartupError("staged skill file inventory is incomplete")

    @staticmethod
    def _request_fingerprint(claim: CodingJobClaim) -> str:
        return coding_job_request_fingerprint(claim.request)

    def _container_name(self, claim: CodingJobClaim) -> str:
        return self._container_name_for(claim.job_id, claim.attempt)

    @staticmethod
    def _container_name_for(job_id: str, attempt: int) -> str:
        digest = sha256(f"{job_id}\0{attempt}".encode()).hexdigest()[:16]
        return f"team-agent-{digest}"

    async def _inspect(self, name: str, expected: Mapping[str, str]) -> str | None:
        inspected = await self._commands.run(
            ["docker", "inspect", "--format", "{{json .Config.Labels}}", name]
        )
        if inspected.exit_code != 0:
            message = inspected.stderr.decode("utf-8", errors="replace")
            lowered = message.lower()
            if "no such object" in lowered or "no such container" in lowered:
                return None
            raise DockerExecutionError("Docker could not inspect the coding runner")
        try:
            labels = json.loads(inspected.stdout)
        except json.JSONDecodeError as error:
            raise DockerExecutionError("Docker returned invalid container labels") from error
        if not isinstance(labels, dict) or any(
            labels.get(key) != value for key, value in expected.items()
        ):
            raise DockerExecutionError("deterministic container name belongs to another attempt")
        state = await self._commands.run(
            ["docker", "inspect", "--format", "{{.State.Status}}", name]
        )
        if state.exit_code != 0:
            raise DockerExecutionError("Docker could not inspect the reconciled runner")
        return state.stdout.decode().strip()

    async def _cleanup_stale_attempts(self, claim: CodingJobClaim) -> None:
        listed = await self._commands.run(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                "label=team-agent.managed=true",
                "--filter",
                f"label=team-agent.job-id={claim.job_id}",
                "--format",
                '{{.Names}}\t{{.Label "team-agent.attempt"}}\t{{.Label "team-agent.namespace"}}',
            ]
        )
        if listed.exit_code != 0:
            raise DockerExecutionError("Docker could not reconcile prior attempts")
        lines = listed.stdout.decode().splitlines()
        if len(lines) > 100:
            raise DockerExecutionError("Docker returned too many prior attempts")
        current_name = self._container_name(claim)
        verifier_name = f"{current_name}-verify"
        for line in lines:
            parts = line.split("\t")
            if len(parts) != 3 or not parts[1].isdigit():
                raise DockerExecutionError("Docker returned malformed attempt labels")
            observed_attempt = int(parts[1])
            expected_name = (
                self._container_name_for(claim.job_id, observed_attempt)
                if parts[2] == "coding-runner"
                else f"{self._container_name_for(claim.job_id, observed_attempt)}-verify"
                if parts[2] == "coding-verifier"
                else None
            )
            if expected_name is None or parts[0] != expected_name:
                raise DockerExecutionError("unexpected container claims a fenced attempt")
            if observed_attempt == claim.attempt and parts[0] == verifier_name:
                # Verification is intentionally restartable rather than attachable. Its
                # input patch is immutable and the credential-free check is deterministic.
                await self.cleanup(parts[0])
            elif observed_attempt == claim.attempt and parts[0] != current_name:
                raise DockerExecutionError("multiple containers claim the same fenced attempt")
            elif observed_attempt != claim.attempt:
                await self.cleanup(parts[0])

    async def reconcile_orphans(self, repository: CodingJobRepository, *, limit: int = 100) -> int:
        if limit < 1 or limit > 1_000:
            raise ValueError("orphan reconciliation limit must be between 1 and 1000")
        listed = await self._commands.run(
            [
                "docker",
                "ps",
                "--all",
                "--filter",
                "label=team-agent.managed=true",
                "--format",
                '{{.Names}}\t{{.Label "team-agent.job-id"}}'
                '\t{{.Label "team-agent.attempt"}}'
                '\t{{.Label "team-agent.namespace"}}',
            ]
        )
        if listed.exit_code != 0:
            raise DockerExecutionError("Docker could not list coding-runner orphans")
        lines = listed.stdout.decode().splitlines()
        if len(lines) > limit:
            raise DockerExecutionError("coding-runner orphan scan exceeded its bound")
        removed = 0
        for line in lines:
            parts = line.split("\t")
            if len(parts) != 4 or not parts[2].isdigit():
                raise DockerExecutionError("Docker returned malformed orphan labels")
            observed_attempt = int(parts[2])
            base_name = self._container_name_for(parts[1], observed_attempt)
            expected_name = (
                base_name
                if parts[3] == "coding-runner"
                else f"{base_name}-verify"
                if parts[3] == "coding-verifier"
                else None
            )
            if parts[0] != expected_name:
                raise DockerExecutionError("Docker returned an unexpected managed container name")
            snapshot = await repository.read(parts[1])
            if (
                snapshot is None
                or snapshot.job.status != CodingJobStatus.RUNNING
                or snapshot.job.attempt != observed_attempt
            ):
                await self.cleanup(parts[0])
                removed += 1
        return removed

    def _create_argv(
        self,
        name: str,
        labels: Mapping[str, str],
        claim: CodingJobClaim,
        input_root: Path,
        artifacts_root: Path,
    ) -> list[str]:
        policy = claim.request.execution_policy
        network = "none" if policy.network == "none" else str(self._model_network)
        argv = [
            "docker",
            "create",
            "--name",
            name,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--pids-limit",
            str(policy.pids_limit),
            "--memory",
            f"{policy.memory_megabytes}m",
            "--cpus",
            str(policy.cpu_count),
            "--network",
            network,
            "--tmpfs",
            "/workspace:rw,exec,nosuid,size=2g,uid=10002,gid=10002,mode=0700",
            "--tmpfs",
            "/run/team-agent:rw,noexec,nosuid,size=16m,uid=10002,gid=10002,mode=0700",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,size=512m,uid=10002,gid=10002,mode=0700",
            "--mount",
            f"type=bind,src={input_root},dst=/input,readonly",
            "--mount",
            f"type=bind,src={artifacts_root},dst=/artifacts",
        ]
        for key, value in sorted(labels.items()):
            argv.extend(["--label", f"{key}={value}"])
        if policy.network == "model_only" and self._codex_home is not None:
            argv.extend(
                ["--mount", f"type=bind,src={self._codex_home},dst=/home/runner/.codex,readonly"]
            )
        if self._fake_codex_executable is not None:
            executable = self._fake_codex_executable
            if executable.is_symlink() or not executable.is_file():
                raise ExecutionStartupError("fake Codex executable is unavailable or unsafe")
            argv.extend(
                [
                    "--mount",
                    f"type=bind,src={executable},dst=/opt/team-agent/fake-codex,readonly",
                    "--env",
                    "TEAM_AGENT_CODEX_EXECUTABLE=/opt/team-agent/fake-codex",
                ]
            )
        argv.extend([self._image, "python", "-m", "team_agent_runtime.coding_runner"])
        return argv

    async def _verify(
        self,
        claim: CodingJobClaim,
        input_root: Path,
        runner_root: Path,
        verifier_root: Path,
    ) -> VerificationReport:
        policy = claim.request.execution_policy
        name = f"{self._container_name(claim)}-verify"
        await self.cleanup(name)
        argv = [
            "docker",
            "create",
            "--name",
            name,
            "--read-only",
            "--cap-drop",
            "ALL",
            "--security-opt",
            "no-new-privileges",
            "--network",
            "none",
            "--pids-limit",
            str(policy.pids_limit),
            "--memory",
            f"{policy.memory_megabytes}m",
            "--cpus",
            str(policy.cpu_count),
            "--tmpfs",
            "/workspace:rw,exec,nosuid,size=2g,uid=10002,gid=10002,mode=0700",
            "--tmpfs",
            "/tmp:rw,exec,nosuid,size=512m,uid=10002,gid=10002,mode=0700",
            "--mount",
            f"type=bind,src={input_root},dst=/input,readonly",
            "--mount",
            f"type=bind,src={runner_root},dst=/runner-artifacts,readonly",
            "--mount",
            f"type=bind,src={verifier_root},dst=/verification",
            "--env",
            "TEAM_AGENT_RUNNER_ARTIFACTS=/runner-artifacts",
            "--env",
            "TEAM_AGENT_VERIFIER_OUTPUT=/verification",
            "--label",
            "team-agent.managed=true",
            "--label",
            "team-agent.namespace=coding-verifier",
            "--label",
            f"team-agent.job-id={claim.job_id}",
            "--label",
            f"team-agent.attempt={claim.attempt}",
            self._image,
            "python",
            "-m",
            "team_agent_runtime.coding_verifier",
        ]
        try:
            created = await self._commands.run(argv)
            status = "created"
            if created.exit_code != 0:
                expected = {
                    "team-agent.managed": "true",
                    "team-agent.namespace": "coding-verifier",
                    "team-agent.job-id": claim.job_id,
                    "team-agent.attempt": str(claim.attempt),
                }
                reconciled = await self._inspect(name, expected)
                if reconciled is None:
                    raise DockerExecutionError("Docker could not create the verifier")
                status = reconciled
            if status == "running":
                verified = await self._commands.run(
                    ["docker", "wait", name],
                    timeout_seconds=policy.timeout_seconds,
                )
                verifier_succeeded = verified.exit_code == 0 and verified.stdout.strip() == b"0"
            else:
                verified = await self._commands.run(
                    ["docker", "start", "--attach", name],
                    timeout_seconds=policy.timeout_seconds,
                )
                verifier_succeeded = verified.exit_code == 0
        finally:
            await self.cleanup(name)
        report = VerificationReport.model_validate_json(
            self._bounded_read(verifier_root / "verification.json", policy.max_output_bytes)
        )
        if verifier_succeeded != report.passed:
            raise DockerExecutionError("verifier exit status disagrees with its report")
        return report

    @staticmethod
    def _outside_editable_paths(paths: list[str], prefixes: list[str]) -> list[str]:
        if not prefixes:
            return []
        return [
            path
            for path in paths
            if not any(path == prefix or path.startswith(f"{prefix}/") for prefix in prefixes)
        ]

    def _persist_artifacts(
        self,
        claim: CodingJobClaim,
        runner_root: Path,
        verification_path: Path,
        durable_root: Path,
    ) -> ExecutionArtifacts:
        names: dict[str, Literal["patch", "codex_jsonl", "final_output", "verification"]] = {
            "changes.patch": "patch",
            "codex.jsonl": "codex_jsonl",
            "final-output.json": "final_output",
            "verification.json": "verification",
        }
        items: list[ExecutionArtifact] = []
        total_limit = claim.request.execution_policy.max_output_bytes
        for filename, kind in names.items():
            path = verification_path if filename == "verification.json" else runner_root / filename
            payload = self._bounded_read(
                path, max(total_limit, claim.request.execution_policy.max_patch_bytes)
            )
            durable_path = durable_root / filename
            self._remove_output(durable_path)
            durable_path.write_bytes(payload)
            items.append(
                ExecutionArtifact(
                    name=kind,
                    path=filename,
                    sha256=f"sha256:{sha256(payload).hexdigest()}",
                    size=len(payload),
                )
            )
        relative_root = str(durable_root.relative_to(self._artifact_root))
        return ExecutionArtifacts(root=relative_root, items=items)

    @staticmethod
    def _remove_output(path: Path) -> None:
        if path.is_symlink() or (path.exists() and not path.is_file()):
            raise DockerExecutionError(f"unsafe artifact path: {path.name}")
        path.unlink(missing_ok=True)

    def _clear_outputs(self, *roots: Path) -> None:
        for root in roots:
            if root.is_symlink() or not root.is_dir():
                raise DockerExecutionError("unsafe artifact directory")
            for child in root.iterdir():
                self._remove_output(child)

    @staticmethod
    def _bounded_read(path: Path, limit: int) -> bytes:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > limit:
            raise DockerExecutionError(f"missing or oversized runner artifact: {path.name}")
        return path.read_bytes()


class LocalDockerExecutor:
    """Claim, heartbeat, validate, persist, and safely clean one Codex attempt."""

    def __init__(
        self,
        repository: CodingJobRepository,
        runner: LocalDockerRunner,
        *,
        worker_id: str,
        lease_ttl: timedelta = DEFAULT_JOB_LEASE_TTL,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not worker_id:
            raise ValueError("worker_id is required")
        self._repository = repository
        self._runner = runner
        self._worker_id = worker_id
        self._lease_ttl = lease_ttl
        self._clock = clock or (lambda: datetime.now(UTC))

    async def execute_one(self) -> CodingRunSnapshot | None:
        now = self._clock()
        claim = await self._repository.recover(
            self._worker_id, now, now + self._lease_ttl, harness="codex"
        )
        if claim is None:
            claim = await self._repository.claim(
                self._worker_id, now, now + self._lease_ttl, harness="codex"
            )
        if claim is None:
            return None
        active_claim: CodingJobClaim = claim
        active_claim = await self._repository.record_execution(
            active_claim,
            ExecutionProgress(phase=ExecutionPhase.PREPARING, detail="Preparing pinned inputs"),
            now,
        )

        async def mark_verifying() -> None:
            nonlocal active_claim
            active_claim = await self._repository.record_execution(
                active_claim,
                ExecutionProgress(
                    phase=ExecutionPhase.VERIFYING,
                    detail="Running independent checks",
                ),
                self._clock(),
            )

        task = asyncio.create_task(self._runner.execute(active_claim, verifying=mark_verifying))
        heartbeat = max(self._lease_ttl.total_seconds() / 3, 0.01)
        try:
            active_claim = await self._repository.record_execution(
                active_claim,
                ExecutionProgress(phase=ExecutionPhase.RUNNING, detail="Codex runner is active"),
                self._clock(),
            )
            while not task.done():
                done, _ = await asyncio.wait({task}, timeout=heartbeat)
                if done:
                    break
                renewed = self._clock()
                active_claim = await self._repository.renew(
                    active_claim, renewed, renewed + self._lease_ttl
                )
            execution = await task
            active_claim = await self._repository.record_execution(
                active_claim,
                ExecutionProgress(
                    phase=ExecutionPhase.PERSISTING,
                    detail="Persisting verified artifacts",
                ),
                self._clock(),
                artifacts=execution.artifacts,
            )
            snapshot = await self._repository.complete(
                active_claim, execution.result, self._clock()
            )
            # Mongo completion is already authoritative and must never be rewritten as failed
            # because post-completion container cleanup was transiently unavailable. Startup
            # orphan reconciliation will retry removal of this terminal attempt.
            with suppress(DockerExecutionError):
                await self._runner.cleanup(execution.container_name)
            return snapshot
        except StaleCodingJobClaimError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            await self._runner.cleanup(self._runner.container_name(active_claim))
            return await self._repository.read(active_claim.job_id)
        except asyncio.CancelledError:
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            raise
        except ExecutionStartupError as error:
            return await self._repository.fail(
                active_claim,
                CodingJobFailure(
                    code="execution_startup_failed", message=str(error), retriable=False
                ),
                self._clock(),
            )
        except TimeoutError:
            timed_out_at = self._clock()
            try:
                return await self._repository.timeout(active_claim, timed_out_at)
            except StaleCodingJobClaimError:
                await self._repository.reconcile(timed_out_at)
                return await self._repository.read(active_claim.job_id)
        except DockerExecutionError as error:
            return await self._repository.fail(
                active_claim,
                CodingJobFailure(code="execution_failed", message=str(error), retriable=True),
                self._clock(),
            )


__all__ = [
    "CommandAdapter",
    "CommandResult",
    "DockerExecutionError",
    "ExecutionStartupError",
    "FrozenCodexSkillStager",
    "LocalDockerExecutor",
    "LocalDockerRunner",
    "LocalRepositoryCatalog",
    "SubprocessCommandAdapter",
]

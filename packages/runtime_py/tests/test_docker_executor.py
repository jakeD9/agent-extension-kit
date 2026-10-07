import asyncio
import hashlib
import json
import subprocess
from collections.abc import Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from team_agent_contracts import Citation, SkillFileManifest, SkillLock, SkillLockPackage
from team_agent_runtime.coding_runner import RunnerRequest
from team_agent_runtime.coding_runner import main as runner_main
from team_agent_runtime.docker_executor import (
    CommandResult,
    ExecutionStartupError,
    FrozenCodexSkillStager,
    LocalDockerExecutor,
    LocalDockerRunner,
    LocalRepositoryCatalog,
    SubprocessCommandAdapter,
)
from team_agent_runtime.execution import ExecutionPolicy
from team_agent_runtime.jobs import (
    CodingJobClaim,
    CodingJobOutcome,
    CodingJobRequest,
    CodingJobStatus,
    InMemoryCodingJobRepository,
)
from team_agent_skills import SkillDistributionError


def _git_repository(root: Path) -> str:
    root.mkdir()
    subprocess.run(["git", "init", "--quiet", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.email", "test@example.com"], check=True)
    subprocess.run(["git", "-C", str(root), "config", "user.name", "Test"], check=True)
    (root / "worker.py").write_text("broken = True\n")
    subprocess.run(["git", "-C", str(root), "add", "worker.py"], check=True)
    subprocess.run(["git", "-C", str(root), "commit", "--quiet", "-m", "fixture"], check=True)
    return subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()


def _lock(revision: str = "content-1") -> SkillLock:
    content = b"# Verified skill\n"
    return SkillLock(
        catalog_revision=revision,
        project="platform",
        packages=[
            SkillLockPackage(
                package_id=f"sha256:{'a' * 64}",
                name="diagnose-and-fix",
                description="Fix a defect.",
                version="1.0.0",
                source_revision=revision,
                files=[
                    SkillFileManifest(
                        path="SKILL.md",
                        sha256=hashlib.sha256(content).hexdigest(),
                        size=len(content),
                    )
                ],
                citation=Citation(
                    repository="company/agent-extension",
                    path="extension/skills/diagnose-and-fix/SKILL.md",
                    revision=revision,
                ),
            )
        ],
    )


def _request(revision: str) -> CodingJobRequest:
    return CodingJobRequest(
        job_id="job-codex",
        run_id="run-codex",
        conversation_id="conversation-1",
        source_turn_id="turn-1",
        submission_idempotency_key="submit-codex",
        project="platform",
        repository="company/service",
        repository_revision=revision,
        objective="Repair worker.py",
        harness="codex",
        execution_policy=ExecutionPolicy(
            editable_paths=["worker.py"], checks=[["python", "-m", "compileall", "worker.py"]]
        ),
        content_revision="content-1",
        selected_skill_lock=_lock(),
        deadline_at=datetime(2026, 1, 1, 1, tzinfo=UTC),
    )


class _Stager:
    def stage(self, generic_lock: SkillLock, root: Path) -> Path:
        assert generic_lock.target == "generic"
        output = root / "installed"
        (output / "diagnose-and-fix").mkdir(parents=True)
        (output / "diagnose-and-fix" / "SKILL.md").write_text("# Verified skill\n")
        (output / "skills.lock.json").write_text(
            generic_lock.model_copy(update={"target": "codex"}).model_dump_json()
        )
        (output / ".team-agent-ownership.json").write_text("{}")
        return output


class _DockerAndGit:
    def __init__(self, *, changed_paths: list[str] | None = None) -> None:
        self.git = SubprocessCommandAdapter()
        self.calls: list[list[str]] = []
        self.created = False
        self.labels: dict[str, str] = {}
        self.artifacts: Path | None = None
        self.verifier_output: Path | None = None
        self.changed_paths = changed_paths or ["worker.py"]
        self.ps_output = b""

    async def run(
        self, argv: Sequence[str], *, timeout_seconds: int | None = None
    ) -> CommandResult:
        del timeout_seconds
        values = list(argv)
        if values[0] == "git":
            return await self.git.run(values)
        self.calls.append(values)
        if values[1] == "ps":
            return CommandResult(0, self.ps_output)
        if values[1] == "inspect" and not self.created:
            return CommandResult(1, stderr=b"Error: No such object")
        if values[1:4] == ["inspect", "--format", "{{json .Config.Labels}}"]:
            return CommandResult(0, json.dumps(self.labels).encode())
        if values[1:4] == ["inspect", "--format", "{{.State.Status}}"]:
            return CommandResult(0, b"created\n")
        if values[1] == "create":
            self.created = True
            self.labels = {}
            for index, value in enumerate(values):
                if value == "--label":
                    key, label_value = values[index + 1].split("=", 1)
                    self.labels[key] = label_value
                if value == "--mount" and "dst=/artifacts" in values[index + 1]:
                    source = values[index + 1].split("src=", 1)[1].split(",", 1)[0]
                    self.artifacts = Path(source)
                if value == "--mount" and "dst=/verification" in values[index + 1]:
                    source = values[index + 1].split("src=", 1)[1].split(",", 1)[0]
                    self.verifier_output = Path(source)
            return CommandResult(0, b"container-id")
        if values[1:3] == ["start", "--attach"]:
            if values[-1].endswith("-verify"):
                assert self.verifier_output is not None
                (self.verifier_output / "verification.json").write_text(
                    json.dumps(
                        {
                            "schema_version": "1",
                            "passed": True,
                            "changed_paths": self.changed_paths,
                            "checks": ["fixture check: exit 0"],
                        }
                    )
                )
                return CommandResult(0)
            assert self.artifacts is not None
            (self.artifacts / "changes.patch").write_text("diff --git a/worker.py b/worker.py\n")
            (self.artifacts / "codex.jsonl").write_text('{"type":"turn.completed"}\n')
            (self.artifacts / "final-output.json").write_text(
                '{"schema_version":"1","outcome":"fixed","summary":"fixed","reusable_lesson":null}'
            )
            (self.artifacts / "runner-result.json").write_text(
                json.dumps(
                    {
                        "schema_version": "1",
                        "outcome": "fixed",
                        "summary": "fixed",
                        "changed_paths": ["worker.py"],
                        "reusable_lesson": None,
                    }
                )
            )
            return CommandResult(0)
        if values[1:3] == ["rm", "--force"]:
            self.created = False
            return CommandResult(0)
        raise AssertionError(values)


class _BlockingDockerAndGit(_DockerAndGit):
    def __init__(self) -> None:
        super().__init__()
        self.started = asyncio.Event()

    async def run(
        self, argv: Sequence[str], *, timeout_seconds: int | None = None
    ) -> CommandResult:
        values = list(argv)
        if values[:3] == ["docker", "start", "--attach"]:
            self.calls.append(values)
            self.started.set()
            await asyncio.Future()
        return await super().run(values, timeout_seconds=timeout_seconds)


class _BlockingVerifierDockerAndGit(_DockerAndGit):
    def __init__(self) -> None:
        super().__init__()
        self.verifier_started = asyncio.Event()

    async def run(
        self, argv: Sequence[str], *, timeout_seconds: int | None = None
    ) -> CommandResult:
        values = list(argv)
        if values[:3] == ["docker", "start", "--attach"] and values[-1].endswith("-verify"):
            self.calls.append(values)
            self.verifier_started.set()
            await asyncio.Future()
        return await super().run(values, timeout_seconds=timeout_seconds)


class _AmbiguousVerifierCreateDockerAndGit(_DockerAndGit):
    async def run(
        self, argv: Sequence[str], *, timeout_seconds: int | None = None
    ) -> CommandResult:
        values = list(argv)
        if values[:2] == ["docker", "create"] and values[values.index("--name") + 1].endswith(
            "-verify"
        ):
            result = await super().run(values, timeout_seconds=timeout_seconds)
            assert result.exit_code == 0
            return CommandResult(1, stderr=b"daemon response was lost")
        return await super().run(values, timeout_seconds=timeout_seconds)


class _FinalRunnerCleanupFailureDockerAndGit(_DockerAndGit):
    async def run(
        self, argv: Sequence[str], *, timeout_seconds: int | None = None
    ) -> CommandResult:
        values = list(argv)
        if values[:3] == ["docker", "rm", "--force"] and not values[-1].endswith("-verify"):
            self.calls.append(values)
            return CommandResult(1, stderr=b"daemon temporarily unavailable")
        return await super().run(values, timeout_seconds=timeout_seconds)


def test_local_repository_catalog_bundles_only_an_exact_commit(tmp_path: Path) -> None:
    async def check() -> None:
        source = tmp_path / "source"
        revision = _git_repository(source)
        catalog = LocalRepositoryCatalog({"company/service": source}, SubprocessCommandAdapter())
        bundle = tmp_path / "repository.bundle"
        await catalog.bundle("company/service", revision, bundle)
        heads = subprocess.check_output(["git", "bundle", "list-heads", str(bundle)], text=True)
        assert heads.startswith(revision)
        bundle.write_bytes(b"corrupt")
        with pytest.raises(ExecutionStartupError, match="wrong revision"):
            await catalog.validate_bundle(revision, bundle)

        try:
            await catalog.bundle("company/service", "HEAD", tmp_path / "bad.bundle")
        except Exception as error:
            assert "full commit" in str(error)
        else:
            raise AssertionError("symbolic revisions must not be accepted")

    asyncio.run(check())


def test_frozen_codex_stager_retargets_generic_lock_and_requires_exact_packages(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    observed: dict[str, object] = {}

    class _Installer:
        def __init__(self, destination: Path, cache: Path, *, project_root: Path) -> None:
            observed.update(
                destination=destination,
                cache=cache,
                project_root=project_root,
            )

        def pull(self, client: object, **kwargs: object) -> object:
            observed.update(client=client, **kwargs)
            return object()

    monkeypatch.setattr("team_agent_runtime.docker_executor.SkillInstaller", _Installer)
    client = object()
    root = tmp_path / "stage"

    destination = FrozenCodexSkillStager(client).stage(_lock(), root)  # type: ignore[arg-type]

    assert destination == root / "project" / ".agents" / "skills"
    assert observed["target"] == "codex"
    assert observed["frozen"] is True
    lock_path = observed["lock_path"]
    assert isinstance(lock_path, Path)
    installed_lock = SkillLock.model_validate_json(lock_path.read_bytes())
    assert installed_lock.target == "codex"
    assert installed_lock.packages == _lock().packages


def test_frozen_codex_stager_maps_exact_package_failure_to_startup_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    class _FailingInstaller:
        def __init__(self, *args: object, **kwargs: object) -> None:
            pass

        def pull(self, *args: object, **kwargs: object) -> object:
            raise SkillDistributionError("exact package unavailable")

    monkeypatch.setattr("team_agent_runtime.docker_executor.SkillInstaller", _FailingInstaller)

    with pytest.raises(ExecutionStartupError, match="locked Codex skills"):
        FrozenCodexSkillStager(object()).stage(  # type: ignore[arg-type]
            _lock(), tmp_path / "stage"
        )


def test_executor_persists_verified_artifacts_before_completion_and_cleans_container(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        source = tmp_path / "source"
        revision = _git_repository(source)
        commands = _DockerAndGit()
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(revision), now)
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({"company/service": source}, commands),
            _Stager(),
            artifact_root=tmp_path / "artifacts",
            image="company/coding-runner:test",
        )
        snapshot = await LocalDockerExecutor(
            repository,
            runner,
            worker_id="worker-1",
            clock=lambda: now + timedelta(seconds=1),
        ).execute_one()

        assert snapshot is not None
        assert snapshot.job.status == CodingJobStatus.COMPLETED
        assert snapshot.job.outcome == CodingJobOutcome.FIXED
        assert snapshot.job.execution_artifacts == snapshot.job.result.artifacts  # type: ignore[union-attr]
        assert snapshot.job.execution_artifacts is not None
        assert snapshot.job.execution_artifacts.root.endswith("/attempt-1/artifacts")
        assert "job-codex-" in snapshot.job.execution_artifacts.root
        assert {item.name for item in snapshot.job.execution_artifacts.items} == {
            "patch",
            "codex_jsonl",
            "final_output",
            "verification",
        }
        create = next(call for call in commands.calls if call[1] == "create")
        assert "--read-only" in create and "--cap-drop" in create
        assert create[create.index("--network") + 1] == "none"
        assert any("uid=10002" in value for value in create)
        verifier = next(
            call
            for call in commands.calls
            if call[1] == "create" and call[call.index("--name") + 1].endswith("-verify")
        )
        assert verifier[verifier.index("--network") + 1] == "none"
        assert not any(".codex" in value for value in verifier)
        runner_mount = next(value for value in verifier if "dst=/runner-artifacts" in value)
        assert runner_mount.endswith(",readonly")
        assert any(call[1:3] == ["rm", "--force"] for call in commands.calls)

    asyncio.run(check())


def test_changed_path_outside_simple_policy_is_completed_as_unsafe(tmp_path: Path) -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        source = tmp_path / "source"
        revision = _git_repository(source)
        commands = _DockerAndGit(changed_paths=["secrets.txt"])
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(revision), now)
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({"company/service": source}, commands),
            _Stager(),
            artifact_root=tmp_path / "artifacts",
            image="company/coding-runner:test",
        )
        snapshot = await LocalDockerExecutor(
            repository,
            runner,
            worker_id="worker-1",
            clock=lambda: now + timedelta(seconds=1),
        ).execute_one()
        assert snapshot is not None
        assert snapshot.job.outcome == CodingJobOutcome.UNSAFE_TO_PROCEED

    asyncio.run(check())


def test_artifact_directories_with_same_sanitized_prefix_do_not_collide(tmp_path: Path) -> None:
    commands = _DockerAndGit()
    runner = LocalDockerRunner(
        commands,
        LocalRepositoryCatalog({}, commands),
        _Stager(),
        artifact_root=tmp_path,
        image="company/coding-runner:test",
    )
    now = datetime(2026, 1, 1, tzinfo=UTC)
    first_request = _request("a" * 40).model_copy(update={"job_id": "same/" + "x" * 100 + "a"})
    second_request = first_request.model_copy(update={"job_id": "same/" + "x" * 100 + "b"})
    first = CodingJobClaim(
        job_id=first_request.job_id,
        run_id=first_request.run_id,
        worker_id="worker",
        attempt=1,
        request=first_request,
        lease_expires_at=now + timedelta(minutes=1),
    )
    second = first.__class__(
        job_id=second_request.job_id,
        run_id=second_request.run_id,
        worker_id="worker",
        attempt=1,
        request=second_request,
        lease_expires_at=now + timedelta(minutes=1),
    )
    assert runner._attempt_root(first) != runner._attempt_root(second)


def test_partial_staged_skill_projection_fails_closed(tmp_path: Path) -> None:
    root = tmp_path / "skills"
    root.mkdir()
    (root / "skills.lock.json").write_text(
        _lock().model_copy(update={"target": "codex"}).model_dump_json()
    )
    (root / ".team-agent-ownership.json").write_text("{}")
    with pytest.raises(ExecutionStartupError, match=r"unexpected entries|unavailable"):
        LocalDockerRunner._validate_skill_projection(root, _lock())


@pytest.mark.parametrize("suffix", ["", "-verify"])
def test_terminal_managed_container_is_removed_by_startup_orphan_reconciliation(
    tmp_path: Path, suffix: str
) -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        source = tmp_path / "source"
        revision = _git_repository(source)
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(revision), now)
        claim = await repository.claim("worker", now, now + timedelta(seconds=30))
        assert claim is not None
        await repository.cancel(claim.job_id, now + timedelta(seconds=1))
        commands = _DockerAndGit()
        commands.created = True
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({"company/service": source}, commands),
            _Stager(),
            artifact_root=tmp_path / "artifacts",
            image="company/coding-runner:test",
        )
        name = f"{runner.container_name(claim)}{suffix}"
        namespace = "coding-verifier" if suffix else "coding-runner"
        commands.ps_output = f"{name}\t{claim.job_id}\t{claim.attempt}\t{namespace}\n".encode()
        assert await runner.reconcile_orphans(repository) == 1
        assert ["docker", "rm", "--force", name] in commands.calls

    asyncio.run(check())


def test_same_attempt_verifier_is_cleaned_before_restart(tmp_path: Path) -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        request = _request("a" * 40)
        claim = CodingJobClaim(
            job_id=request.job_id,
            run_id=request.run_id,
            worker_id="worker",
            attempt=1,
            request=request,
            lease_expires_at=now + timedelta(minutes=1),
        )
        commands = _DockerAndGit()
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({}, commands),
            _Stager(),
            artifact_root=tmp_path,
            image="company/coding-runner:test",
        )
        verifier_name = f"{runner.container_name(claim)}-verify"
        commands.ps_output = f"{verifier_name}\t{claim.attempt}\tcoding-verifier\n".encode()

        await runner._cleanup_stale_attempts(claim)

        assert ["docker", "rm", "--force", verifier_name] in commands.calls

    asyncio.run(check())


def test_ambiguous_verifier_create_is_reconciled(tmp_path: Path) -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        source = tmp_path / "source"
        revision = _git_repository(source)
        commands = _AmbiguousVerifierCreateDockerAndGit()
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(revision), now)
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({"company/service": source}, commands),
            _Stager(),
            artifact_root=tmp_path / "artifacts",
            image="company/coding-runner:test",
        )

        snapshot = await LocalDockerExecutor(
            repository,
            runner,
            worker_id="worker",
            clock=lambda: now + timedelta(seconds=1),
        ).execute_one()

        assert snapshot is not None
        assert snapshot.job.status == CodingJobStatus.COMPLETED
        assert any(
            call[1:3] == ["start", "--attach"] and call[-1].endswith("-verify")
            for call in commands.calls
        )

    asyncio.run(check())


def test_cleanup_failure_after_completion_does_not_rewrite_terminal_state(
    tmp_path: Path,
) -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        source = tmp_path / "source"
        revision = _git_repository(source)
        commands = _FinalRunnerCleanupFailureDockerAndGit()
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(revision), now)
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({"company/service": source}, commands),
            _Stager(),
            artifact_root=tmp_path / "artifacts",
            image="company/coding-runner:test",
        )

        snapshot = await LocalDockerExecutor(
            repository,
            runner,
            worker_id="worker",
            clock=lambda: now + timedelta(seconds=1),
        ).execute_one()

        assert snapshot is not None
        assert snapshot.job.status == CodingJobStatus.COMPLETED
        persisted = await repository.read(snapshot.job.job_id)
        assert persisted is not None
        assert persisted.job.status == CodingJobStatus.COMPLETED

    asyncio.run(check())


def test_executor_maps_policy_timeout_to_timed_out_lifecycle(tmp_path: Path) -> None:
    class _TimeoutRunner:
        async def execute(self, claim: CodingJobClaim, *, verifying: object = None) -> object:
            del claim, verifying
            raise TimeoutError

        async def cleanup(self, container_name: str) -> None:
            del container_name

        def container_name(self, claim: CodingJobClaim) -> str:
            return claim.job_id

    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        repository = InMemoryCodingJobRepository()
        request = _request("a" * 40)
        await repository.submit(request, now)
        snapshot = await LocalDockerExecutor(
            repository,
            _TimeoutRunner(),  # type: ignore[arg-type]
            worker_id="worker",
            clock=lambda: now + timedelta(seconds=1),
        ).execute_one()
        assert snapshot is not None
        assert snapshot.job.status == CodingJobStatus.TIMED_OUT
        assert snapshot.job.failure is None

    asyncio.run(check())


def test_executor_cancellation_terminates_and_removes_active_container(tmp_path: Path) -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        source = tmp_path / "source"
        revision = _git_repository(source)
        commands = _BlockingDockerAndGit()
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(revision), now)
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({"company/service": source}, commands),
            _Stager(),
            artifact_root=tmp_path / "artifacts",
            image="company/coding-runner:test",
        )
        task = asyncio.create_task(
            LocalDockerExecutor(
                repository,
                runner,
                worker_id="worker",
                clock=lambda: now + timedelta(seconds=1),
            ).execute_one()
        )
        await commands.started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert any(call[1:3] == ["rm", "--force"] for call in commands.calls)

    asyncio.run(check())


def test_executor_cancellation_removes_named_verifier_and_runner(tmp_path: Path) -> None:
    async def check() -> None:
        now = datetime(2026, 1, 1, tzinfo=UTC)
        source = tmp_path / "source"
        revision = _git_repository(source)
        commands = _BlockingVerifierDockerAndGit()
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(revision), now)
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({"company/service": source}, commands),
            _Stager(),
            artifact_root=tmp_path / "artifacts",
            image="company/coding-runner:test",
        )
        task = asyncio.create_task(
            LocalDockerExecutor(
                repository,
                runner,
                worker_id="worker",
                clock=lambda: now + timedelta(seconds=1),
            ).execute_one()
        )
        await commands.verifier_started.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        removed = [call[-1] for call in commands.calls if call[1:3] == ["rm", "--force"]]
        assert any(name.endswith("-verify") for name in removed)
        assert any(not name.endswith("-verify") for name in removed)

    asyncio.run(check())


def test_network_off_runner_executes_a_fake_codex_with_pinned_checkout(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    source = tmp_path / "source"
    revision = _git_repository(source)
    input_root = tmp_path / "input"
    artifact_root = tmp_path / "artifacts"
    workspace = tmp_path / "workspace"
    input_root.mkdir()
    artifact_root.mkdir()
    asyncio.run(
        LocalRepositoryCatalog({"company/service": source}, SubprocessCommandAdapter()).bundle(
            "company/service", revision, input_root / "repository.bundle"
        )
    )
    (input_root / "request.json").write_text(
        RunnerRequest(schema_version="1", attempt=1, request=_request(revision)).model_dump_json()
    )
    (input_root / "skills" / "diagnose-and-fix").mkdir(parents=True)
    (input_root / "skills" / "diagnose-and-fix" / "SKILL.md").write_text("# Skill\n")
    fake = tmp_path / "fake-codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "out = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
        "out.write_text(json.dumps({'schema_version':'1','outcome':'fixed',"
        "'summary':'fake repair','reusable_lesson':None}))\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--cd') + 1])\n"
        "(workspace / 'new-file.py').write_text('created = True\\n')\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    fake.chmod(0o755)
    monkeypatch.setenv("TEAM_AGENT_RUNNER_INPUT", str(input_root))
    monkeypatch.setenv("TEAM_AGENT_RUNNER_ARTIFACTS", str(artifact_root))
    monkeypatch.setenv("TEAM_AGENT_RUNNER_WORKSPACE", str(workspace))
    monkeypatch.setenv("TEAM_AGENT_OUTPUT_SCHEMA", str(tmp_path / "schema.json"))
    monkeypatch.setenv("TEAM_AGENT_CODEX_EXECUTABLE", str(fake))

    assert runner_main() == 0
    result = json.loads((artifact_root / "runner-result.json").read_text())
    assert result["summary"] == "fake repair"
    assert result["changed_paths"] == ["new-file.py"]
    assert b"new-file.py" in (artifact_root / "changes.patch").read_bytes()
    assert (workspace / ".agents/skills/diagnose-and-fix/SKILL.md").is_file()

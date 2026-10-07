import os
import subprocess
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pytest
from team_agent_contracts import Citation, SkillFileManifest, SkillLock, SkillLockPackage
from team_agent_runtime.docker_executor import (
    LocalDockerExecutor,
    LocalDockerRunner,
    LocalRepositoryCatalog,
    SubprocessCommandAdapter,
)
from team_agent_runtime.execution import ExecutionPolicy
from team_agent_runtime.jobs import CodingJobRequest, CodingJobStatus, InMemoryCodingJobRepository

SKILL = b"# Fixture skill\n"


class _VerifiedSkillFixture:
    def stage(self, generic_lock: SkillLock, root: Path) -> Path:
        output = root / "installed"
        (output / "diagnose-and-fix").mkdir(parents=True)
        (output / "diagnose-and-fix" / "SKILL.md").write_bytes(SKILL)
        (output / "skills.lock.json").write_text(
            generic_lock.model_copy(update={"target": "codex"}).model_dump_json()
        )
        (output / ".team-agent-ownership.json").write_text("{}")
        return output


def _request(revision: str) -> CodingJobRequest:
    content_revision = "fixture-content"
    lock = SkillLock(
        catalog_revision=content_revision,
        project="fixture",
        packages=[
            SkillLockPackage(
                package_id=f"sha256:{'a' * 64}",
                name="diagnose-and-fix",
                description="Fixture skill",
                version="1.0.0",
                source_revision=content_revision,
                files=[
                    SkillFileManifest(
                        path="SKILL.md", sha256=sha256(SKILL).hexdigest(), size=len(SKILL)
                    )
                ],
                citation=Citation(
                    repository="fixture/context",
                    path="skills/diagnose-and-fix/SKILL.md",
                    revision=content_revision,
                ),
            )
        ],
    )
    return CodingJobRequest(
        job_id="live-docker-fixture",
        run_id="live-docker-run",
        conversation_id="live-docker-conversation",
        source_turn_id="live-docker-turn",
        submission_idempotency_key="live-docker-submit",
        project="fixture",
        repository="fixture/repository",
        repository_revision=revision,
        objective="Repair worker.py",
        harness="codex",
        execution_policy=ExecutionPolicy(
            network="none",
            editable_paths=["worker.py"],
            checks=[["python", "-m", "compileall", "worker.py"]],
        ),
        content_revision=content_revision,
        selected_skill_lock=lock,
        deadline_at=datetime.now(UTC) + timedelta(minutes=5),
    )


def test_opt_in_coding_runner_image_contains_the_pinned_codex_cli() -> None:
    if os.environ.get("RUN_LIVE_CODING_RUNNER_TESTS") != "1":
        pytest.skip("set RUN_LIVE_CODING_RUNNER_TESTS=1")
    image = os.environ.get("TEAM_AGENT_CODING_RUNNER_IMAGE")
    if not image:
        pytest.skip("TEAM_AGENT_CODING_RUNNER_IMAGE is not configured")
    completed = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "codex",
            image,
            "--version",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == "codex-cli 0.154.0-alpha.6.2"
    help_result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network",
            "none",
            "--entrypoint",
            "codex",
            image,
            "exec",
            "--help",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )
    assert help_result.returncode == 0, help_result.stderr
    for option in (
        "--ephemeral",
        "--ignore-user-config",
        "--strict-config",
        "--sandbox",
        "--approve-for-me",
        "--json",
        "--output-schema",
        "--output-last-message",
    ):
        assert option in help_result.stdout


def test_opt_in_real_docker_fake_codex_repairs_pinned_fixture(tmp_path: Path) -> None:
    if os.environ.get("RUN_LIVE_CODING_RUNNER_TESTS") != "1":
        pytest.skip("set RUN_LIVE_CODING_RUNNER_TESTS=1")
    image = os.environ.get("TEAM_AGENT_CODING_RUNNER_IMAGE")
    if not image:
        pytest.skip("TEAM_AGENT_CODING_RUNNER_IMAGE is not configured")
    source = tmp_path / "source"
    source.mkdir()
    subprocess.run(["git", "init", "--quiet", str(source)], check=True)
    subprocess.run(
        ["git", "-C", str(source), "config", "user.email", "test@example.com"], check=True
    )
    subprocess.run(["git", "-C", str(source), "config", "user.name", "Test"], check=True)
    (source / "worker.py").write_text("broken = True\n")
    subprocess.run(["git", "-C", str(source), "add", "worker.py"], check=True)
    subprocess.run(["git", "-C", str(source), "commit", "--quiet", "-m", "fixture"], check=True)
    revision = subprocess.check_output(
        ["git", "-C", str(source), "rev-parse", "HEAD"], text=True
    ).strip()
    fake = tmp_path / "fake-codex"
    fake.write_text(
        "#!/usr/bin/env python3\n"
        "import json, pathlib, sys\n"
        "workspace = pathlib.Path(sys.argv[sys.argv.index('--cd') + 1])\n"
        "(workspace / 'worker.py').write_text('broken = False\\n')\n"
        "output = pathlib.Path(sys.argv[sys.argv.index('--output-last-message') + 1])\n"
        "output.write_text(json.dumps({'schema_version':'1','outcome':'fixed',"
        "'summary':'fixture repaired','reusable_lesson':None}))\n"
        "print(json.dumps({'type':'turn.completed'}))\n"
    )
    fake.chmod(0o755)

    async def exercise() -> None:
        now = datetime.now(UTC)
        repository = InMemoryCodingJobRepository()
        await repository.submit(_request(revision), now)
        commands = SubprocessCommandAdapter()
        runner = LocalDockerRunner(
            commands,
            LocalRepositoryCatalog({"fixture/repository": source}, commands),
            _VerifiedSkillFixture(),
            artifact_root=tmp_path / "artifacts",
            image=image,
            fake_codex_executable=fake,
        )
        snapshot = await LocalDockerExecutor(
            repository, runner, worker_id="live-docker-worker"
        ).execute_one()
        assert snapshot is not None
        assert snapshot.job.status == CodingJobStatus.COMPLETED, snapshot.job.failure
        assert snapshot.job.result is not None
        assert snapshot.job.result.changed_paths == ["worker.py"]
        assert snapshot.job.execution_artifacts is not None

    import asyncio

    asyncio.run(exercise())

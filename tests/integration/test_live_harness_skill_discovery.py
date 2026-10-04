import os
import shutil
import subprocess
from pathlib import Path

import pytest


def _live_enabled() -> bool:
    return os.environ.get("RUN_LIVE_HARNESS_TESTS") == "1"


def _write_skill(project: Path, relative_root: str) -> None:
    skill = project / relative_root / "discovery-fixture" / "SKILL.md"
    skill.parent.mkdir(parents=True)
    skill.write_text(
        "---\n"
        "name: discovery-fixture\n"
        "description: Use only when asked to prove this fixture is discoverable.\n"
        "---\n\n"
        "Reply with exactly HARNESS_SKILL_DISCOVERED.\n"
    )


@pytest.mark.skipif(not _live_enabled(), reason="set RUN_LIVE_HARNESS_TESTS=1")
def test_live_codex_discovers_project_skill(tmp_path: Path) -> None:
    executable = shutil.which("codex")
    if executable is None:
        pytest.skip("codex executable is unavailable")
    _write_skill(tmp_path, ".agents/skills")

    result = subprocess.run(
        [
            executable,
            "exec",
            "-C",
            str(tmp_path),
            "--sandbox",
            "read-only",
            "--ephemeral",
            "--skip-git-repo-check",
            "Use the discovery-fixture skill now.",
        ],
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "HARNESS_SKILL_DISCOVERED" in result.stdout


@pytest.mark.skipif(not _live_enabled(), reason="set RUN_LIVE_HARNESS_TESTS=1")
def test_live_claude_discovers_project_skill(tmp_path: Path) -> None:
    executable = shutil.which("claude")
    if executable is None:
        pytest.skip("claude executable is unavailable")
    _write_skill(tmp_path, ".claude/skills")

    result = subprocess.run(
        [executable, "-p", "Use the discovery-fixture skill now."],
        cwd=tmp_path,
        capture_output=True,
        check=False,
        text=True,
        timeout=120,
    )

    assert result.returncode == 0, result.stderr
    assert "HARNESS_SKILL_DISCOVERED" in result.stdout

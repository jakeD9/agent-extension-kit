"""Credential-free independent patch verifier for the coding-runner image."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

from team_agent_runtime.coding_runner import RunnerRequest
from team_agent_runtime.execution import VerificationReport


def _run(argv: list[str], *, cwd: Path | None = None) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        argv,
        cwd=cwd,
        capture_output=True,
        check=False,
    )


def main() -> int:
    input_root = Path(os.environ.get("TEAM_AGENT_RUNNER_INPUT", "/input"))
    artifact_root = Path(os.environ.get("TEAM_AGENT_RUNNER_ARTIFACTS", "/runner-artifacts"))
    output_root = Path(os.environ.get("TEAM_AGENT_VERIFIER_OUTPUT", "/verification"))
    workspace = Path(os.environ.get("TEAM_AGENT_RUNNER_WORKSPACE", "/workspace"))
    request = RunnerRequest.model_validate_json((input_root / "request.json").read_bytes())
    clone = _run(
        ["git", "clone", "--no-checkout", str(input_root / "repository.bundle"), str(workspace)]
    )
    observations: list[str] = []
    ready = clone.returncode == 0
    if ready:
        checkout = _run(
            ["git", "checkout", "--detach", request.request.repository_revision], cwd=workspace
        )
        observations.append(f"git checkout: exit {checkout.returncode}")
        ready = checkout.returncode == 0
    patch = artifact_root / "changes.patch"
    if ready and patch.stat().st_size:
        applied = _run(["git", "apply", "--check", "--index", str(patch)], cwd=workspace)
        observations.append(f"git apply --check: exit {applied.returncode}")
        ready = applied.returncode == 0
        if ready:
            applied = _run(["git", "apply", "--index", str(patch)], cwd=workspace)
            observations.append(f"git apply: exit {applied.returncode}")
            ready = applied.returncode == 0
    changed_paths: list[str] = []
    if ready:
        changed = _run(["git", "diff", "--name-only", "-z", "HEAD"], cwd=workspace)
        if changed.returncode == 0:
            changed_paths = sorted(
                value.decode("utf-8") for value in changed.stdout.split(b"\0") if value
            )
        else:
            ready = False
    for argv in request.request.execution_policy.checks if ready else []:
        label = json.dumps(argv)[:1_900]
        if any(item in {"", ".."} or "\x00" in item for item in argv):
            observations.append(f"{label}: exit 126")
            ready = False
            continue
        completed = _run(argv, cwd=workspace)
        observations.append(f"{label}: exit {completed.returncode}")
        ready = ready and completed.returncode == 0
    passed = ready
    output_root.mkdir(parents=True, exist_ok=True)
    report = VerificationReport(
        schema_version="1",
        passed=passed,
        changed_paths=changed_paths,
        checks=observations,
    )
    (output_root / "verification.json").write_text(report.model_dump_json())
    return 0 if passed else 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as error:
        print(str(error), file=sys.stderr)
        raise SystemExit(1) from None

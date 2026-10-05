import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_docker_workspace_copies_every_uv_workspace_member() -> None:
    workspace = tomllib.loads((ROOT / "pyproject.toml").read_text())["tool"]["uv"]["workspace"]
    dockerfile = (ROOT / "Dockerfile").read_text()

    for member in workspace["members"]:
        assert f"COPY {member} ./{member}" in dockerfile


def test_root_pytest_collects_provider_neutral_runtime_contracts() -> None:
    config = tomllib.loads((ROOT / "pyproject.toml").read_text())

    assert "packages/runtime_py/tests" in config["tool"]["pytest"]["ini_options"]["testpaths"]

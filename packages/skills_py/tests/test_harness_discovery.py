import json
from pathlib import Path
from typing import Literal, cast

import pytest
from team_agent_skills import harness_skill_directory

FIXTURE = Path(__file__).parents[3] / "tests/fixtures/harness-skill-discovery.json"
Target = Literal["codex", "claude"]


@pytest.mark.parametrize("target", ["codex", "claude"])
def test_project_target_matches_pinned_harness_discovery_fixture(
    tmp_path: Path, target: Target
) -> None:
    fixture = json.loads(FIXTURE.read_text())
    expected = fixture["harnesses"][target]["project_skill_directory"]

    destination = harness_skill_directory(tmp_path, target)

    assert destination.relative_to(tmp_path).as_posix() == expected
    assert destination.is_relative_to(tmp_path)


def test_harness_discovery_fixture_records_local_verification_limits() -> None:
    fixture = json.loads(FIXTURE.read_text())

    assert fixture["schema_version"] == "1"
    assert fixture["harnesses"]["codex"]["version"] == "codex-cli 0.154.0-alpha.6.2"
    assert fixture["harnesses"]["claude"]["version"] == "not-installed"
    assert cast(str, fixture["harnesses"]["claude"]["verification"]).startswith("fixture")

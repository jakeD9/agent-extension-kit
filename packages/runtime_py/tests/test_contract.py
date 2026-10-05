import pytest
from pydantic import ValidationError
from team_agent_runtime import AgentTurnRequest


def test_turn_request_is_narrow_strict_and_snake_case() -> None:
    request = AgentTurnRequest(
        run_id="run-1",
        project="platform",
        objective="Use the selected procedure and cite the answer.",
        skill_names=["incident-guide"],
    )

    assert request.model_dump(mode="json") == {
        "schema_version": "1",
        "run_id": "run-1",
        "project": "platform",
        "objective": "Use the selected procedure and cite the answer.",
        "skill_names": ["incident-guide"],
    }
    with pytest.raises(ValidationError):
        AgentTurnRequest(
            run_id="run-1",
            project="platform",
            objective="answer",
            skill_names=["incident-guide", "incident-guide"],
        )

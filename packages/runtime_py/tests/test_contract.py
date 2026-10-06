import pytest
from pydantic import ValidationError
from team_agent_runtime import AgentTurnRequest, ConversationContext, ConversationTurnContext


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
        "conversation_context": None,
    }
    with pytest.raises(ValidationError):
        AgentTurnRequest(
            run_id="run-1",
            project="platform",
            objective="answer",
            skill_names=["incident-guide", "incident-guide"],
        )


def test_turn_request_accepts_only_bounded_conversation_context() -> None:
    request = AgentTurnRequest(
        run_id="run-2",
        project="platform",
        objective="What should I do next?",
        skill_names=["incident-guide"],
        conversation_context=ConversationContext(
            summary="The worker was restarted.",
            recent_turns=[
                ConversationTurnContext(
                    sequence=2,
                    user="Did it recover?",
                    assistant="The health check is now passing.",
                )
            ],
        ),
    )

    assert request.model_dump(mode="json")["conversation_context"] == {
        "summary": "The worker was restarted.",
        "recent_turns": [
            {
                "sequence": 2,
                "user": "Did it recover?",
                "assistant": "The health check is now passing.",
            }
        ],
    }
    with pytest.raises(ValidationError):
        ConversationContext(
            summary="",
            recent_turns=[
                ConversationTurnContext(sequence=index + 1, user="u", assistant="a")
                for index in range(7)
            ],
        )
    with pytest.raises(ValidationError):
        ConversationContext(
            summary="s" * 12_000,
            recent_turns=[
                ConversationTurnContext(
                    sequence=1,
                    user="u" * 18_001,
                    assistant="a" * 18_000,
                )
            ],
        )

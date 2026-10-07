import json
from pathlib import Path

import pytest
from pydantic import ValidationError
from team_agent_runtime.codex_harness import (
    CodexHarnessError,
    codex_exec_argv,
    parse_codex_jsonl,
    read_codex_final,
)
from team_agent_runtime.execution import ExecutionPolicy


def test_codex_command_is_ephemeral_strict_structured_and_noninteractive() -> None:
    argv = codex_exec_argv(executable="/opt/fake-codex")

    assert argv[0] == "/opt/fake-codex"
    assert "--ephemeral" in argv
    assert "--ignore-user-config" in argv
    assert "--strict-config" in argv
    assert argv[argv.index("--sandbox") + 1] == "workspace-write"
    assert "--approve-for-me" in argv
    assert "--json" in argv
    assert "--output-schema" in argv
    assert argv[-1] == "-"
    assert "--dangerously-bypass-approvals-and-sandbox" not in argv


def test_codex_jsonl_and_final_output_are_bounded_and_strict(tmp_path: Path) -> None:
    assert parse_codex_jsonl(b'{"type":"turn.started"}\n', max_bytes=100) == [
        {"type": "turn.started"}
    ]
    with pytest.raises(CodexHarnessError, match="line 1"):
        parse_codex_jsonl(b"not-json\n", max_bytes=100)
    with pytest.raises(CodexHarnessError, match="limit"):
        parse_codex_jsonl(b"{}\n", max_bytes=1)

    final = tmp_path / "final.json"
    final.write_text(
        json.dumps(
            {
                "schema_version": "1",
                "outcome": "fixed",
                "summary": "Repaired the worker.",
                "reusable_lesson": None,
            }
        )
    )
    assert read_codex_final(final, max_bytes=1_000).summary == "Repaired the worker."
    final.write_text('{"outcome":"fixed","summary":"invalid","unexpected":true}')
    with pytest.raises(CodexHarnessError, match="invalid"):
        read_codex_final(final, max_bytes=1_000)
    final.write_text('{"schema_version":"1","outcome":"fixed","summary":"missing field"}')
    with pytest.raises(CodexHarnessError, match="invalid"):
        read_codex_final(final, max_bytes=1_000)


def test_execution_policy_has_only_bounded_argv_checks_and_simple_path_prefixes() -> None:
    policy = ExecutionPolicy(
        editable_paths=["src", "tests/unit"],
        checks=[["pytest", "tests/unit"], ["ruff", "check", "src"]],
    )
    assert policy.network == "none"
    assert policy.editable_paths == ["src", "tests/unit"]
    with pytest.raises(ValidationError, match="editable_paths"):
        ExecutionPolicy(editable_paths=["../outside"])
    with pytest.raises(ValidationError, match="checks"):
        ExecutionPolicy(checks=[[""]])

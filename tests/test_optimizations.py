from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from bareloop.compact import reactive_compact
from bareloop.loop import execute_agent_loop
from bareloop.mode import AgentMode
from bareloop.settings import DEFAULT_MODE, DEFAULT_MODEL, DEFAUlT_MODEL
from bareloop.tools.dispatcher import dispatch_tool_result
from bareloop.tools.registry import get_tool
from bareloop.worktree import remove_worktree


def test_naming_and_entrypoint_aliases() -> None:
    from bareloop import main

    assert main.create_session is not None
    assert main.init_agent is not None
    assert main.main is not None
    assert DEFAULT_MODE == AgentMode.NORMAL
    assert DEFAULT_MODEL == AgentMode.NORMAL
    assert DEFAUlT_MODEL == AgentMode.NORMAL


def test_subagent_tool_name_and_alias() -> None:
    tool_primary = get_tool("spawn_subagent")
    tool_legacy = get_tool("spaw_subagent")

    assert tool_primary is not None
    assert tool_legacy is not None
    assert tool_primary.parameters["properties"]["query"]["type"] == "string"


def test_dispatcher_rejects_invalid_arguments_against_schema() -> None:
    # 'write' requires 'path' and 'content', both strings
    res_missing = dispatch_tool_result("write", {"path": "test.txt"})
    assert res_missing.outcome == "error"
    assert res_missing.error_kind == "invalid_arguments"
    assert "content" in res_missing.output

    res_wrong_type = dispatch_tool_result("write", {"path": "test.txt", "content": 12345})
    assert res_wrong_type.outcome == "error"
    assert res_wrong_type.error_kind == "invalid_arguments"
    assert "is not of type 'string'" in res_wrong_type.output

    # unknown extra parameter when additionalProperties is False
    res_extra = dispatch_tool_result(
        "write", {"path": "test.txt", "content": "ok", "unexpected_field": True}
    )
    assert res_extra.outcome == "error"
    assert res_extra.error_kind == "invalid_arguments"
    assert "unexpected_field" in res_extra.output


def test_reactive_compact_truncates_large_tool_outputs() -> None:
    long_content = "X" * 1500
    messages = [
        {"role": "user", "content": "run task"},
        {"role": "tool", "tool_call_id": "call_1", "content": long_content},
    ]

    compacted = reactive_compact(messages)
    assert len(compacted) == 2
    assert compacted[1]["role"] == "tool"
    # Result should have been persisted or truncated
    assert len(compacted[1]["content"]) < len(long_content)


def test_loop_recovers_from_context_overflow_with_reactive_compact() -> None:
    call_count = 0

    class MockCompletions:
        def create(self, **_kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise Exception("Error: context length exceeded (maximum context is 4096 tokens)")
            msg = SimpleNamespace(content="Recovered answer", tool_calls=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    mock_client = SimpleNamespace(chat=SimpleNamespace(completions=MockCompletions()))

    class DummyTokenizer:
        def apply_chat_template(self, *args, **kwargs):
            return [1, 2, 3]

    messages = [
        {"role": "user", "content": "analyze code"},
        {"role": "tool", "tool_call_id": "call_0", "content": "Y" * 800},
    ]

    result = execute_agent_loop(
        messages,
        client=mock_client,
        model="test-model",
        tokenizer=DummyTokenizer(),
        workdir=None,
        max_rounds=2,
    )

    assert result.completed is True
    assert result.final_output == "Recovered answer"
    assert call_count == 2  # Proves that it retried after reactive compaction


def test_worktree_lifecycle_cleanup(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    # Validation error on bad name
    assert "Invalid worktree name" in remove_worktree("..bad")
    # Non-existent worktree
    assert "does not exist" in remove_worktree("non_existent_wt_test")

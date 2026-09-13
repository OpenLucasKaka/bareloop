from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bareloop.session import AgentSession


class FakeTokenizer:
    def apply_chat_template(self, *_args, **_kwargs):
        return []


class FakeClient:
    def __init__(self, responses):
        self._responses = iter(responses)
        self.requests = []
        self.chat = SimpleNamespace(
            completions=SimpleNamespace(create=self._create),
        )

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        response = next(self._responses)
        if isinstance(response, BaseException):
            raise response
        return response


def model_response(content: str, tool_calls=None, usage=None):
    return SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(content=content, tool_calls=tool_calls),
            )
        ],
        usage=usage,
    )


def make_session(
    tmp_path: Path,
    responses,
    *,
    max_rounds: int = 4,
    telemetry_path: Path | None = None,
) -> AgentSession:
    return AgentSession(
        workdir=tmp_path,
        system_prompt="system",
        client_instance=FakeClient(responses),
        model="test-model",
        tokenizer_instance=FakeTokenizer(),
        max_rounds=max_rounds,
        trace=None,
        telemetry_path=telemetry_path,
    )


def test_sessions_own_independent_messages(tmp_path: Path) -> None:
    first = make_session(tmp_path, [model_response("first")])
    second = make_session(tmp_path, [model_response("second")])

    first.run("one")

    assert first.messages is not second.messages
    assert [message["content"] for message in first.messages] == ["system", "one", "first"]
    assert second.messages == [{"role": "system", "content": "system"}]


def test_no_tool_response_completes_with_real_metrics(tmp_path: Path) -> None:
    session = make_session(tmp_path, [model_response("done")])

    result = session.run("finish")

    assert result.completed is True
    assert result.final_output == "done"
    assert result.messages == session.messages
    assert result.messages is not session.messages
    assert result.rounds == 1
    assert result.tool_calls == 0
    assert result.duration_ms >= 0
    assert result.error is None
    assert result.termination == "completed"


def test_session_persists_telemetry_snapshot(tmp_path: Path) -> None:
    telemetry_path = tmp_path / "telemetry" / "runs.jsonl"
    session = make_session(
        tmp_path,
        [model_response("done")],
        telemetry_path=telemetry_path,
    )

    session.run("finish")

    record = json.loads(telemetry_path.read_text(encoding="utf-8"))
    assert record["model"] == "test-model"
    assert record["termination"] == "completed"
    assert record["provider_requests"] == 1


def test_session_records_provider_usage_for_every_round(tmp_path: Path) -> None:
    tool_call = SimpleNamespace(
        id="call_missing",
        function=SimpleNamespace(name="missing_tool", arguments="{}"),
    )
    first_usage = SimpleNamespace(prompt_tokens=10, completion_tokens=3, total_tokens=13)
    second_usage = SimpleNamespace(prompt_tokens=20, completion_tokens=5, total_tokens=25)
    session = make_session(
        tmp_path,
        [
            model_response("working", [tool_call], first_usage),
            model_response("done", usage=second_usage),
        ],
    )

    result = session.run("finish")

    assert result.model == "test-model"
    assert result.telemetry.provider_request_count == 2
    assert result.telemetry.provider_success_count == 2
    assert result.telemetry.token_usage_available is True
    assert result.telemetry.input_tokens == 30
    assert result.telemetry.output_tokens == 8
    assert result.telemetry.total_tokens == 38
    assert len(result.telemetry.provider_calls) == 2
    assert all(call.duration_ms >= 0 for call in result.telemetry.provider_calls)


def test_session_does_not_estimate_missing_provider_usage(tmp_path: Path) -> None:
    session = make_session(tmp_path, [model_response("done")])

    result = session.run("finish")

    assert result.telemetry.provider_request_count == 1
    assert result.telemetry.provider_success_count == 1
    assert result.telemetry.token_usage_available is False
    assert result.telemetry.input_tokens is None
    assert result.telemetry.output_tokens is None
    assert result.telemetry.total_tokens is None


def test_session_exposes_only_eval_tools_to_model(tmp_path: Path) -> None:
    session = make_session(tmp_path, [model_response("done")])

    session.run("finish")

    tool_names = {tool["function"]["name"] for tool in session.client.requests[0]["tools"]}
    assert tool_names == {"read", "write", "edit", "glob"}


def test_session_rejects_forged_tool_call_outside_eval_allowlist(tmp_path: Path) -> None:
    tool_call = SimpleNamespace(
        id="call_bash",
        function=SimpleNamespace(
            name="bash",
            arguments='{"command":"touch forbidden.txt"}',
        ),
    )
    session = make_session(
        tmp_path,
        [model_response("", [tool_call]), model_response("done")],
    )

    result = session.run("finish")

    assert result.completed is True
    assert not (tmp_path / "forbidden.txt").exists()
    assert any(
        message.get("content") == "Error: tool 'bash' is not allowed in this session"
        for message in result.messages
    )
    assert result.telemetry.tool_calls[0].name == "bash"
    assert result.telemetry.tool_calls[0].outcome == "blocked"
    assert result.telemetry.tool_calls[0].error_kind == "allowlist"


def test_session_counts_blocked_workspace_escape_as_security_block(tmp_path: Path) -> None:
    tool_call = SimpleNamespace(
        id="call_escape",
        function=SimpleNamespace(
            name="write",
            arguments='{"path":"../escaped.txt","content":"forbidden"}',
        ),
    )
    session = make_session(
        tmp_path,
        [model_response("", [tool_call]), model_response("done")],
    )

    result = session.run("finish")

    assert result.completed is True
    assert result.telemetry.security_blocks == 1
    assert result.telemetry.safety_violations == 0
    assert not (tmp_path.parent / "escaped.txt").exists()


def test_run_result_messages_do_not_change_after_later_run(tmp_path: Path) -> None:
    session = make_session(
        tmp_path,
        [model_response("first"), model_response("second")],
    )
    first_result = session.run("one")
    first_messages = list(first_result.messages)

    session.run("two")

    assert first_result.messages == first_messages


def test_mutating_result_messages_does_not_change_session(tmp_path: Path) -> None:
    session = make_session(tmp_path, [model_response("done")])
    result = session.run("finish")

    result.messages[0]["content"] = "changed"
    result.messages.append({"role": "user", "content": "injected"})

    assert session.messages[0]["content"] == "system"
    assert all(message.get("content") != "injected" for message in session.messages)


def test_max_rounds_returns_incomplete_result(tmp_path: Path) -> None:
    tool_call = SimpleNamespace(
        id="call_one",
        function=SimpleNamespace(name="missing_tool", arguments="{}"),
    )
    session = make_session(
        tmp_path,
        [model_response("working", tool_calls=[tool_call])],
        max_rounds=1,
    )

    result = session.run("finish")

    assert result.completed is False
    assert result.rounds == 1
    assert result.tool_calls == 1
    assert "maximum rounds" in result.error.lower()
    assert result.termination == "max_rounds"


def test_provider_error_returns_incomplete_result(tmp_path: Path) -> None:
    session = make_session(tmp_path, [RuntimeError("provider unavailable")])

    result = session.run("finish")

    assert result.completed is False
    assert result.rounds == 1
    assert result.tool_calls == 0
    assert result.final_output == ""
    assert result.error == "RuntimeError: provider unavailable"
    assert result.termination == "provider_error"


def test_invalid_tool_arguments_return_incomplete_result(tmp_path: Path) -> None:
    tool_call = SimpleNamespace(
        id="call_bad_json",
        function=SimpleNamespace(name="missing_tool", arguments="{not json"),
    )
    session = make_session(
        tmp_path,
        [model_response("working", tool_calls=[tool_call])],
    )

    result = session.run("finish")

    assert result.completed is False
    assert result.rounds == 1
    assert result.tool_calls == 1
    assert result.final_output == "working"
    assert "JSONDecodeError" in result.error
    assert "Expecting property name" in result.error
    assert result.telemetry.tool_calls[0].outcome == "invalid"
    assert result.telemetry.tool_calls[0].error_kind == "invalid_arguments"
    assert result.termination == "invalid_tool_call"


def test_max_rounds_must_be_positive(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="max_rounds must be at least 1"):
        make_session(tmp_path, [], max_rounds=0)

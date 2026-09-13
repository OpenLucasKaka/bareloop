from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from bareloop.goal import (
    GoalController,
    GoalDecision,
    GoalStatus,
    OpenAIGoalEvaluator,
    parse_goal_decision,
)


def test_first_instruction_stays_condition_and_later_input_is_feedback() -> None:
    controller = GoalController(lambda *_args: GoalDecision(True, "done"))

    first = controller.accept_user_input("Fix login and run tests")
    first.status = GoalStatus.COMPLETED
    second = controller.accept_user_input("It still fails; continue")

    assert second is first
    assert second.condition == "Fix login and run tests"
    assert second.feedback == ["It still fails; continue"]
    assert second.status == GoalStatus.ACTIVE


def test_leaving_goal_mode_clears_persistent_goal() -> None:
    controller = GoalController(lambda *_args: GoalDecision(True, "done"))
    controller.accept_user_input("first goal")

    controller.leave_goal_mode()
    controller.enter_goal_mode()
    state = controller.accept_user_input("next goal")

    assert state.condition == "next goal"
    assert state.feedback == []


def test_incomplete_decision_blocks_until_cap_then_returns_control() -> None:
    controller = GoalController(
        lambda *_args: GoalDecision(False, "more work"),
        max_incomplete_checks=2,
    )
    state = controller.accept_user_input("finish task")

    first = controller.evaluate_stop([{"role": "assistant", "content": "attempt 1"}])
    second = controller.evaluate_stop([{"role": "assistant", "content": "attempt 2"}])

    assert first == GoalDecision(False, "more work")
    assert second.impossible is True
    assert "limit reached" in second.reason
    assert state.status == GoalStatus.BLOCKED
    assert state.iterations == 2


def test_evaluator_error_blocks_goal_without_claiming_completion() -> None:
    def fail(*_args):
        raise RuntimeError("provider unavailable")

    controller = GoalController(fail)
    state = controller.accept_user_input("finish task")

    decision = controller.evaluate_stop([])

    assert decision.ok is False
    assert decision.impossible is True
    assert "provider unavailable" in decision.reason
    assert state.status == GoalStatus.BLOCKED


@pytest.mark.parametrize(
    "content",
    [
        "not-json",
        "[]",
        '{"ok": true, "reason": "done", "impossible": true}',
        '{"ok": false, "reason": "", "impossible": false}',
    ],
)
def test_parse_goal_decision_rejects_malformed_contract(content: str) -> None:
    with pytest.raises(ValueError):
        parse_goal_decision(content)


def test_live_evaluator_uses_separate_messages_and_persistent_goal_payload() -> None:
    calls = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content='{"ok": false, "reason": "run tests", "impossible": false}'
                )
            )
        ]
    )
    api_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(create=lambda **kwargs: calls.append(kwargs) or response)
        )
    )
    evaluator = OpenAIGoalEvaluator(api_client=api_client, model="test-model")
    evidence = [{"role": "tool", "content": "edited login.py"}]

    decision = evaluator("fix login", ["still broken"], evidence)

    assert decision == GoalDecision(False, "run tests")
    assert calls[0]["model"] == "test-model"
    assert calls[0]["messages"][0]["role"] == "system"
    payload = json.loads(calls[0]["messages"][1]["content"])
    assert payload == {
        "goal": "fix login",
        "feedback": ["still broken"],
        "evidence": evidence,
    }


def test_only_direct_goal_mode_input_updates_goal(monkeypatch: pytest.MonkeyPatch) -> None:
    from bareloop import mian

    controller = GoalController(lambda *_args: GoalDecision(True, "done"))
    messages = [{"role": "system", "content": "system"}]
    calls = []
    monkeypatch.setattr(mian, "trigger_hook", lambda *_args: None)
    monkeypatch.setattr(mian, "agent_loop", lambda *args: calls.append(args))

    mian.run_agent_turn_locked(
        messages,
        SimpleNamespace(),
        "fix login",
        mian.AgentMode.GOAL,
        controller,
    )
    mian.run_agent_turn_locked(
        messages,
        SimpleNamespace(),
        None,
        mian.AgentMode.GOAL,
        controller,
    )

    assert controller.active is not None
    assert controller.active.condition == "fix login"
    assert controller.active.feedback == []
    assert len(calls) == 2


def _prepare_agent_loop(monkeypatch: pytest.MonkeyPatch, contents: list[str]):
    from bareloop import loop

    class NoLoading:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

    responses = iter(
        SimpleNamespace(
            choices=[SimpleNamespace(message=SimpleNamespace(content=content, tool_calls=None))]
        )
        for content in contents
    )
    model_calls = []

    def create(**kwargs):
        model_calls.append(kwargs)
        return next(responses)

    monkeypatch.setattr(loop, "ModelLoading", NoLoading)
    monkeypatch.setattr(loop, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(loop, "load_memories", lambda _messages: "")
    monkeypatch.setattr(loop, "inject_background_results", lambda _messages: None)
    monkeypatch.setattr(loop, "tool_budget_result", lambda messages: messages)
    monkeypatch.setattr(loop, "micro_compact", lambda messages: messages)
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(
        loop,
        "tokenizer",
        SimpleNamespace(apply_chat_template=lambda *_args, **_kwargs: []),
    )
    monkeypatch.setattr(
        loop.client,
        "chat",
        SimpleNamespace(completions=SimpleNamespace(create=create)),
    )
    return loop, model_calls


def test_incomplete_goal_continues_same_loop_then_finalizes_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loop, model_calls = _prepare_agent_loop(monkeypatch, ["attempt one", "done"])
    decisions = iter(
        [
            GoalDecision(False, "run tests"),
            GoalDecision(True, "verified"),
        ]
    )
    controller = GoalController(lambda *_args: next(decisions))
    controller.accept_user_input("fix login")
    stop_events = []
    memory_turns = []
    monkeypatch.setattr(
        loop,
        "trigger_hook",
        lambda event, *_args: stop_events.append(event) or None,
    )
    monkeypatch.setattr(
        loop,
        "schedule_memory_maintenance",
        lambda messages: memory_turns.append(messages),
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "fix login"},
    ]

    loop.agent_loop(
        messages,
        SimpleNamespace(write=lambda **_kwargs: None),
        loop.AgentMode.GOAL,
        controller,
    )

    assert len(model_calls) == 2
    assert (
        sum(message.get("content", "").startswith("[Goal still active]") for message in messages)
        == 1
    )
    assert stop_events == ["Stop"]
    assert len(memory_turns) == 1
    assert controller.active is not None
    assert controller.active.status == GoalStatus.COMPLETED


def test_impossible_goal_returns_control_without_finalizers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    loop, model_calls = _prepare_agent_loop(monkeypatch, ["need user input"])
    controller = GoalController(
        lambda *_args: GoalDecision(False, "missing credentials", impossible=True)
    )
    controller.accept_user_input("deploy service")
    stop_events = []
    memory_turns = []
    monkeypatch.setattr(
        loop,
        "trigger_hook",
        lambda event, *_args: stop_events.append(event) or None,
    )
    monkeypatch.setattr(
        loop,
        "schedule_memory_maintenance",
        lambda messages: memory_turns.append(messages),
    )

    loop.agent_loop(
        [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "deploy service"},
        ],
        SimpleNamespace(write=lambda **_kwargs: None),
        loop.AgentMode.GOAL,
        controller,
    )

    assert len(model_calls) == 1
    assert stop_events == []
    assert memory_turns == []
    assert "[goal blocked] missing credentials" in capsys.readouterr().out

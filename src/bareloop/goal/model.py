from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from bareloop.settings import PRIMARY_MODEL, client

from .prompt_version import GOAL_GATE_SYSTEM_PROMPT

DEFAULT_STOP_HOOK_MAX_CHECKS = 8


class GoalStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    BLOCKED = "blocked"


@dataclass
class GoalState:
    condition: str
    feedback: list[str] = field(default_factory=list)
    status: GoalStatus = GoalStatus.ACTIVE
    iterations: int = 0
    consecutive_incomplete_checks: int = 0
    last_reason: str | None = None


@dataclass(frozen=True)
class GoalDecision:
    ok: bool
    reason: str
    impossible: bool = False


GoalEvaluator = Callable[[str, list[str], list[dict[str, Any]]], GoalDecision]


def parse_goal_decision(content: str) -> GoalDecision:
    try:
        value = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise ValueError("goal evaluator returned invalid JSON") from exc
    if not isinstance(value, dict):
        raise ValueError("goal evaluator response must be an object")

    ok = value.get("ok")
    reason = value.get("reason")
    impossible = value.get("impossible", False)
    if not isinstance(ok, bool):
        raise ValueError("goal evaluator field 'ok' must be a boolean")
    if not isinstance(reason, str) or not reason.strip():
        raise ValueError("goal evaluator field 'reason' must be a non-empty string")
    if not isinstance(impossible, bool):
        raise ValueError("goal evaluator field 'impossible' must be a boolean")
    if ok and impossible:
        raise ValueError("a completed goal cannot be impossible")
    return GoalDecision(ok=ok, reason=reason.strip(), impossible=impossible)


class OpenAIGoalEvaluator:
    def __init__(self, api_client: Any = client, model: str | None = PRIMARY_MODEL):
        self.api_client = api_client
        self.model = model

    def __call__(
        self,
        condition: str,
        feedback: list[str],
        evidence: list[dict[str, Any]],
    ) -> GoalDecision:
        payload = json.dumps(
            {"goal": condition, "feedback": feedback, "evidence": evidence},
            ensure_ascii=False,
            default=str,
        )
        response = self.api_client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": GOAL_GATE_SYSTEM_PROMPT},
                {"role": "user", "content": payload},
            ],
        )
        content = response.choices[0].message.content
        if not content:
            raise ValueError("goal evaluator returned empty content")
        return parse_goal_decision(content)


class GoalController:
    def __init__(
        self,
        evaluator: GoalEvaluator,
        max_incomplete_checks: int = DEFAULT_STOP_HOOK_MAX_CHECKS,
    ):
        if max_incomplete_checks < 1:
            raise ValueError("max_incomplete_checks must be greater than 0")
        self.evaluator = evaluator
        self.max_incomplete_checks = max_incomplete_checks
        self.active: GoalState | None = None

    def enter_goal_mode(self) -> None:
        self.active = None

    def leave_goal_mode(self) -> None:
        self.active = None

    def accept_user_input(self, content: str) -> GoalState:
        normalized = content.strip()
        if not normalized:
            raise ValueError("goal input must be non-empty")
        if self.active is None:
            self.active = GoalState(condition=normalized)
        else:
            self.active.feedback.append(normalized)
            self.active.status = GoalStatus.ACTIVE
            self.active.consecutive_incomplete_checks = 0
            self.active.last_reason = None
        return self.active

    def evaluate_stop(self, evidence: list[dict[str, Any]]) -> GoalDecision:
        state = self.active
        if state is None:
            raise RuntimeError("cannot evaluate without an active goal")
        try:
            decision = self.evaluator(state.condition, list(state.feedback), evidence)
            if decision.ok and decision.impossible:
                raise ValueError("a completed goal cannot be impossible")
        except Exception as exc:
            decision = GoalDecision(
                ok=False,
                reason=f"Goal evaluator failed: {type(exc).__name__}: {exc}",
                impossible=True,
            )

        state.last_reason = decision.reason
        if decision.ok:
            state.status = GoalStatus.COMPLETED
            state.consecutive_incomplete_checks = 0
            return decision
        if decision.impossible:
            state.status = GoalStatus.BLOCKED
            return decision

        state.iterations += 1
        state.consecutive_incomplete_checks += 1
        if state.consecutive_incomplete_checks >= self.max_incomplete_checks:
            state.status = GoalStatus.BLOCKED
            state.last_reason = (
                "Goal continuation limit reached after "
                f"{self.max_incomplete_checks} blocked stop attempts"
            )
            return GoalDecision(ok=False, reason=state.last_reason, impossible=True)

        state.status = GoalStatus.ACTIVE
        return decision

    def continuation_message(self, reason: str) -> str:
        state = self.active
        if state is None:
            raise RuntimeError("cannot continue without an active goal")
        feedback = "\n".join(f"- {item}" for item in state.feedback) or "- None"
        return (
            "[Goal still active]\n"
            f"Original goal: {state.condition}\n"
            f"User feedback:\n{feedback}\n"
            f"Evaluator: {reason}\n"
            "Continue working toward the original goal."
        )


def create_goal_controller() -> GoalController:
    return GoalController(OpenAIGoalEvaluator())


def stop_goal_gate(
    controller: GoalController,
    evidence: list[dict[str, Any]],
) -> GoalDecision:
    return controller.evaluate_stop(evidence)

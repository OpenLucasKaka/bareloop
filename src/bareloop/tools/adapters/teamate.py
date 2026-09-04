from pathlib import Path

from bareloop.agent_team.index import (
    BUS,
    ProtocolState,
    active_teammates,
    new_request_id,
    pending_requests,
    plan_gates,
    plan_request_ids,
    spawn_teammate_thread,
    team_lock,
)
from bareloop.worktree import _run_git, create_worktree


def run_spawn_teammate(
    name: str,
    role: str,
    prompt: str,
    task_id: str | None = None,
    require_plan: bool = False,
) -> str:
    return spawn_teammate_thread(name, role, prompt, task_id, require_plan)


def run_git(args: list[str], cwd: Path | None = None):
    ok, output = _run_git(args, cwd)
    return ok, output[:5000]


def run_list_teammates():
    with team_lock:
        teammates = list(active_teammates.items())
    if not teammates:
        return "没有活跃的agent"
    return "\n".join(f"{name}: {status}" for name, status in teammates)


def run_send_messages(to: str, content: str) -> str:
    with team_lock:
        if to not in active_teammates:
            return f"{to}不在活跃状态"
        BUS.send("lead", to, content)
    return f"消息已发送至{to}"


def run_request_shutdown(teammate: str):
    with team_lock:
        if teammate not in active_teammates:
            return f"{teammate}不在活跃状态"
        request_id = new_request_id()
        pending_requests[request_id] = ProtocolState(
            request_id=request_id,
            type="shutdown",
            sender="lead",
            target=teammate,
            status="pending",
            payload="",
        )
        try:
            BUS.send(
                "lead",
                teammate,
                "Finish the current step and shut down.",
                "shutdown_request",
                {"request_id": request_id},
            )
        except Exception:
            pending_requests.pop(request_id, None)
            raise
    return f"已创建关闭请求 {teammate} {request_id}"


def run_request_plan(teammate: str, task: str):
    with team_lock:
        if teammate not in active_teammates:
            return f"{teammate} 不在活跃状态"
        if plan_gates.get(teammate) == "pending":
            return "A plan is already waiting for review."
        plan_gates[teammate] = "required"
        BUS.send("lead", teammate, task, "plan_request")
    return f"已要求 {teammate} 提交 plan"


def run_create_worktree(name: str, task_id: str, cwd: Path | None = None):
    return create_worktree(name, task_id)


def run_review_plan(request_id: str, approve: bool, feedback: str = ""):
    with team_lock:
        state = pending_requests.get(request_id)
        if state is None:
            return f"Error: unknown plan request {request_id}"
        if state.type != "plan_approval" or state.target != "lead":
            return f"Error: {request_id} is not a plan review request"
        if plan_request_ids.get(state.sender) != request_id:
            return f"Error: stale plan request {request_id}"
        if state.status != "pending":
            return f"Error: plan request {request_id} is already {state.status}"
        state.status = "approved" if approve else "rejected"
        teammate = state.sender
        status = state.status
    content = feedback or f"Plan {status}."
    try:
        BUS.send(
            "lead",
            teammate,
            content,
            "plan_approval_response",
            {"request_id": request_id, "approve": approve},
        )
    except Exception:
        with team_lock:
            state.status = "pending"
        raise
    return f"Plan {request_id} {status}"

from pathlib import Path

from bareloop.agent_team.index import (
    active_teammates,
    team_lock,
    pending_requests,
    plan_gates
)
from bareloop.worktree import _run_git, create_worktree
from bareloop.agent_team import (
    spawn_teammate_thread,
    BUS,
    new_request_id,
    ProtocolState
)


def run_spawn_teammate(name: str, role: str, prompt: str, task_id: str | None = None,
                       require_plan: bool = False) -> str:
    return spawn_teammate_thread(name, role, prompt, task_id, require_plan)



def run_git(args: list[str], cwd: Path | None = None):
    ok, output = _run_git(args, cwd)
    return ok, output[:5000]


def run_list_teammates():
    if not active_teammates:
        return "没有活跃的agent"
    return '\n'.join(f"{agent.name}: {agent.status}" for agent in active_teammates.items())


def run_send_messages(to: str, content: str) -> str:
    if to not in active_teammates:
        return f"{to}不在活跃状态"
    BUS.send("lead", to, content)
    return f"消息已发送至{to}"


def run_request_shutdown(agent: str):
    if agent not in active_teammates:
        return f"{agent}不在活跃状态"
    with team_lock:
        request_id = new_request_id
        pending_requests[request_id] = ProtocolState(
            request_id=request_id,
            type="shutdown",
            sender="lead",
            target=agent,
            status="pending",
            payload="",
        )
        BUS.send('lead', agent, "Finish the current step and shut down.",
                 "shutdown_request", {"request_id": request_id})
        return f"已关闭请求{agent} {request_id}"


def run_request_plan(agent: str, task):
    if agent not in active_teammates:
        return f"{agent} 不在活跃状态"
    with team_lock:
        plan_gates[agent] = "required"
    BUS.send('lead', agent, task, "plan_request")
    return f"已创建{agent} require request"


def run_create_worktree(name: str, task_id: str, cwd: Path | None = None):
    return create_worktree(name, task_id)

def run_review_plan(requst_id: str):
    pass

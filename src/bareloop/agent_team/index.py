import json
import random
import re
import threading
import time
from pathlib import Path
from bareloop.agent_team.config import TEAMMATE_TOOLS
from bareloop.hook import trigger_hook
from bareloop.settings import WORKDIR
from bareloop.task_system import load_task, claim_task, owner_in_progress, task_lock, complete_task, save_task, Task, \
    list_tasks
from bareloop.tools.adapters.task import run_list_tasks
from bareloop.tools.shell import run_bash
from bareloop.tools.filesystem import run_read, run_write, run_edit, run_glob
from bareloop.worktree import teammate_assignment_info, assignment_cwd
from dataclasses import dataclass, field
from bareloop.settings import client, PRIMARY_MODEL, DENY_LIST, DESTRUCTIVE
from bareloop.worktree.index import task_worktree_cwd



MAIL_ROOT = WORKDIR / '.bareloop' / '.mailboxes'
VALID_AGENT_NAME = re.compile(r"^[A-Za-z0-9_-]{1,64}$")
MAILBOX_ROOT = MAIL_ROOT.resolve()
RESERVED_TEAMMATE_NAMES = {"lead", "agent"}
team_lock = threading.RLock()
# 记录 teammate 当前是 working、idle 还是 waiting_approval
active_teammates: dict[str, str] = {}
# 记录它是否必须先经过 Plan 审批
plan_gates: dict[str, str] = {}
plan_request_ids: dict[str, str] = {}
# 记录当前任务分配版本，防止旧任务的审批结果错误应用到新任务
assignment_versions: dict[str, int] = {}
IDLE_SCAN_INTERVAL = 2.0
teammate_threads: dict[str, threading.Thread] = {}

@dataclass
class ProtocolState:
    request_id: str
    type: str
    sender: str
    target: str
    status: str
    payload: str
    work_version: int | None = None
    task_id: str | None = None
    created_at: float = field(default_factory=time.time())


# 已存在的request_id
pending_requests: dict[str, ProtocolState] = {}

def is_validate_name(agent: str):
    return VALID_AGENT_NAME.fullmatch(agent)

class MessageBus:
    def __init__(self):
        self._lock = threading.Lock()
        self._change = threading.Condition(self._lock)

    def _path(self, agent: str):
        if not is_validate_name(agent):
            raise ValueError(f"Invalid mailbox recipient: {agent!r}")
        path = (MAILBOX_ROOT / f"{agent.lower()}.jsonl").resolve()
        if not path.is_relative_to(MAILBOX_ROOT):
            raise ValueError(f"Mailbox path escapes directory: {agent!r}")
        return path

    def read_inbox(self, agent: str) -> list[str]:
        inbox = self._path(agent)
        if not inbox.exists():
            return []
        with self._lock:
            msg = [json.load(l) for l in inbox.read_text().splitlines() if l.strip()]
            inbox.unlink()
        return msg

    def send(self, from_agent: str, to_agent: str, content: str,  msg_type: str = 'mesages', metadata: dict | None = None):
        msg = {
            "from": from_agent, "to": to_agent, "content": content,
            "type": msg_type, "metadata": metadata or {}
        }
        with self._change:
            MAIL_ROOT.mkdir(parents=True, exist_ok=True)
            with self._path(to_agent).open('a', encoding='utf-8') as handle:
                handle.write(json.dumps(msg, ensure_ascii=True) + "\n")
                self._change.notify_all()
        print(f"  [bus] {from_agent} -> {to_agent}: "
             f"({msg_type}) {content[:50]}")

    def peek(self, agent: str) -> bool:
        with self._lock:
            inbox = self._path(agent)
            return inbox.exists() and inbox.stat().st_size > 0

    def wait_for_messages(self, agent: str, timeout: float | None = None) -> list[str]:
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._change:
            while not self.peek(agent):
                remain = None if deadline is None else deadline - time.monotonic()
                if remain is not None and remain < 0:
                    return []
                self._change.wait()
            return self.read_inbox(agent)


BUS = MessageBus()


class TeammateRuntime:
    # require_plan: teammate 是否必须先提交执行计划并等待 Lead 批准
    def __init__(self, name: str, role: str, prompt: str, task_id: str | None, require_plan: bool):
        self.name = name
        self.system = (
            f""""
            你是“{name}”，担任“{role}”一职。请使用工具完成指定的任务，
            随后调用 `complete_task` 并汇报简要结果。
            如果用户的首条消息中包含“[Assigned task]”，则表明该任务已被认领，请勿再次调用 `claim_task`。
            当被要求提供计划时，请调用 `submit_plan` 并等待批准，然后再执行 bash 命令或修改文件。
            文件和 shell 工具将在任务的工作目录下运行；该目录并非沙盒环境。运行时环境会将你的最终文本提交给“Lead”（负责人）。
            仅在进行中间协调时使用 `send_message`，并称呼协调员为“lead”。
"""
        )
        self.messages = [{"role": "user", "content": self.system}]
        if task_id:
            task = load_task(task_id)
            cwd = assignment_cwd(name)
            self.messages[0]["content"] += (
                f"\n\n[Assigned task {task.id}] {task.subject}\n"
                f"{task.description}\nWork directory: {cwd}"
            )
            if require_plan:
                self.messages[0]["content"] += (
                    "\n\n[Plan required] Submit a plan and wait for Lead approval "
                    "before changing files or using bash."
                )
            self.handlers = {
                "bash": self.bash,
                "read_file": self.read,
                "write_file": self.write,
                "edit_file": self.edit,
                "glob": self.glob,
                "send_message": lambda to, content: _teammate_send_message(
                    name, to, content),
                "submit_plan": lambda plan: _teammate_submit_plan(name, plan),
                "list_tasks": run_list_tasks,
                "claim_task": self.claim,
                "complete_task": self.complete,
            }

    def current_cwd(self) -> tuple[Path | None, str | None]:
        if self.name not in teammate_assignment_info:
            return None, "Error: 需要先分配task才能调用工具"
        try:
            return assignment_cwd(self.name), None
        except (FileNotFoundError, ValueError) as exc:
            return None, f"Error: Invalid task assignment: {exc}"

    def bash(self, command: str) -> str:
        cwd, error = self.current_cwd()
        return error or run_bash()

    def read(self, path: str, limit: int | None = None) -> str:
        cwd, error = self.current_cwd()
        return error or run_read(path, limit=limit, cwd=cwd)

    def write(self, path: str, content: str) -> str:
        cwd, error = self.current_cwd()
        return error or run_write(path, content, cwd=cwd)

    def edit(self, path: str, old_text: str, new_text: str) -> str:
        cwd, error = self.current_cwd()
        return error or run_edit(path, old_text, new_text, cwd=cwd)

    def glob(self, pattern: str) -> str:
        cwd, error = self.current_cwd()
        return error or run_glob(pattern, cwd=cwd)


    def claim(self, task_id: str) -> str:
        try:
            return claim_task(task_id, owner=self.name)
        except ValueError as exc:
            return f"Error: {exc}"
        except FileNotFoundError:
            return f"Error: Task {task_id} not found"

    def complete(self, task_id: str) -> str:
        try:
            return complete_task(task_id, owner=self.name)
        except ValueError as exc:
            return f"Error: {exc}"
        except FileNotFoundError:
            return f"Error: Task {task_id} not found"

    def handle_inbox(self, inbox: list[dict]) -> bool:
        """Append work messages and return True for a valid shutdown."""
        work_messages = []
        for msg in inbox:
            msg_type = msg.get("type", "message")
            if msg_type == "shutdown_request":
                accepted, notice = apply_shutdown_request(self.name, msg)
                if not accepted:
                    work_messages.append(notice)
                    continue
                BUS.send(self.name, "lead", "Shutdown acknowledged.",
                         "shutdown_response",
                         {"request_id": notice, "approve": True})
                return True
            if msg_type == "plan_approval_response":
                _, notice = apply_plan_response(self.name, msg)
                work_messages.append(notice)
                continue
            if msg_type == "plan_request":
                work_messages.append(f"[Plan required] {msg['content']}")
                continue
            work_messages.append(
                f"[Message from {msg['from']}] {msg['content']}"
            )
        if work_messages:
            self.messages.append({"role": "user",
                                  "content": "\n".join(work_messages)})
        return False

    def work(self) -> str:
        """Run one model turn. Return continue, idle, or stop."""
        if self.handle_inbox(BUS.read_inbox(self.name)):
            return "stop"
        with team_lock:
            active_teammates[self.name] = "working"
        try:
            response = client.messages.create(
                model=PRIMARY_MODEL,
                system=self.system,
                messages=self.messages,
                tools=TEAMMATE_TOOLS,
                max_tokens=8000,
            )
        except Exception as exc:
            BUS.send(self.name, "lead",
                     f"{type(exc).__name__}: {exc}", "error")
            return "stop"

        self.messages.append({"role": "assistant",
                              "content": response.content})
        if response.stop_reason == "tool_use":
            results = []
            for block in response.content:
                if block.type != "tool_use":
                    continue
                output = _run_teammate_tool(
                    self.name, block, self.handlers
                )
                results.append({"type": "tool_result",
                                "tool_use_id": block.id,
                                "content": output})
            self.messages.append({"role": "user", "content": results})
            return "continue"

        summary = _last_assistant_text(response.content)
        gate = plan_gates.get(self.name, "not_required")
        if gate != "pending" and summary:
            BUS.send(self.name, "lead", summary, "result")
        if gate == "pending":
            with team_lock:
                active_teammates[self.name] = "waiting_approval"
        else:
            release_completed_assignment(self.name)
            with team_lock:
                active_teammates[self.name] = "idle"
            BUS.send(self.name, "lead", "Waiting for more work.",
                     "idle_notification")
        return "idle"

    def wait_for_work(self) -> bool:
        """Wait for a message or atomically claim the next ready Task."""
        while True:
            inbox = BUS.wait_for_messages(self.name, IDLE_SCAN_INTERVAL)
            if inbox:
                before = len(self.messages)
                if self.handle_inbox(inbox):
                    return False
                if len(self.messages) > before:
                    return True
                continue

            task = claim_next_task(self.name)
            if not task:
                continue
            cwd = assignment_cwd(self.name)
            self.messages.append({
                "role": "user",
                "content": (
                    f"[Auto-claimed task {task.id}] {task.subject}\n"
                    f"{task.description}\nWork directory: {cwd}"
                ),
            })
            print(f"  [idle] {self.name} claimed {task.id}: {task.subject}")
            return True

    def run(self):
        try:
            state = "continue"
            while state != "stop":
                if state == "idle" and not self.wait_for_work():
                    break
                state = self.work()
        except Exception as exc:
            try:
                BUS.send(self.name, "lead",
                         f"{type(exc).__name__}: {exc}", "error")
            except Exception:
                pass
        finally:
            try:
                release_teammate_assignment(self.name)
            except Exception as exc:
                try:
                    BUS.send(
                        self.name, "lead",
                        f"Assignment cleanup failed: {type(exc).__name__}: {exc}",
                        "error",
                    )
                except Exception:
                    pass
            with team_lock:
                active_teammates.pop(self.name, None)
                plan_gates.pop(self.name, None)
                plan_request_ids.pop(self.name, None)
                teammate_threads.pop(self.name, None)
            print(f"  [teammate] {self.name} finished")

def match_response(response_type: str, request_id: str, approve: bool,
                   from_agent: str, to_agent: str) -> bool:
    """Match one protocol response to one pending request."""
    with team_lock:
        state = pending_requests.get(request_id)
        if not state:
            print(f"  [protocol] unknown request_id: {request_id}")
            return False
        expected = {
            "shutdown": "shutdown_response",
            "plan_approval": "plan_approval_response",
        }[state.type]
        if response_type != expected:
            print(f"  [protocol] expected {expected}, got {response_type}")
            return False
        if from_agent != state.target or to_agent != state.sender:
            print(f"  [protocol] {request_id} responder mismatch")
            return False
        if state.status != "pending":
            print(f"  [protocol] {request_id} already {state.status}")
            return False
        state.status = "approved" if approve else "rejected"
    print(f"  [protocol] {request_id} -> {state.status}")
    return True


def consume_lead_inbox():
    msgs = BUS.read_inbox('lead')
    for msg in msgs:
        metadata = msg.get("metadata", {})
        request_id = metadata.get("request_id", "")
        if request_id and msg.get("type", "").endswith("_response"):
            match_response(msg["type"], request_id,
                           metadata.get("approve", False),
                           msg.get("from", ""), msg.get("to", ""))
    return msgs


def spawn_teammate_thread(name: str, role: str, prompt: str, task_id: str | None = None, require_plan: bool = False):
    # 判断agent的name格式
    if not is_validate_name(name):
        return ("Invalid teammate name: use 1-64 letters, digits, "
                "underscores, or dashes")
    # 禁止使用内置agent name
    if name.lower() in RESERVED_TEAMMATE_NAMES:
        return f"Invalid teammate name: '{name}' is reserved by the runtime"
    with team_lock:
        if any(existing.casefold() == name.casefold() for existing in active_teammates):
            return f"Teammate '{name}' already exists"
        active_teammates[name] = "working"
        plan_gates[name] = "required" if require_plan else "not_required"
        assignment_versions[name] = 0
        # 如果传了 task_id，会在启动线程之前调用 claim_task()
        if task_id:
            try:
                # 分配任务 防止后续创建runtime时因agent存在任务抛出异常
                claimed_task = claim_task(task_id, owner=name)
            except (FileNotFoundError, ValueError) as e:
                claimed_task = f"Error: {e}"
            # if not claimed_task.startswith('Claimed'):


def new_request_id():
    while True:
        request_id = f"req_{random.randint(0, 999999):06d}"
        if request_id not in pending_requests:
            return request_id


def _teammate_send_message(from_name: str, to: str, content: str):
    with team_lock:
        if to != 'lead' and to not in active_teammates:
            return f"{to} 不在活跃状态"
        BUS.send(from_name, to, content)
        return f"已发送至{to}"

def _teammate_submit_plan(from_name: str, plan: str):
    with team_lock:
        assignment = teammate_assignment_info[from_name]
        task_id = str(assignment["task_id"]) if assignment else None
        work_version = assignment_versions.get(task_id, 0)
        with team_lock:
            if plan_gates.get(from_name) == "pending":
                return "A plan is already waiting for review."
            request_id = new_request_id()
            pending_requests[request_id] = ProtocolState(
                request_id=request_id,
                type="plan_approval",
                sender=from_name,
                target="lead",
                status="pending",
                payload=plan,
                work_version=work_version,
                task_id=task_id,
            )
            plan_gates[from_name] = "pending"
            plan_request_ids[from_name] = request_id
            active_teammates[from_name] = "waiting_approval"
        BUS.send(from_name, "lead", plan, "plan_approval_request",
                    {"request_id": request_id})
        return f"Plan submitted ({request_id}). Wait for Lead's decision."

def release_completed_assignment(owner: str) -> bool:
    """Release a completed cwd lease only at a model turn boundary."""
    with task_lock:
        assignment = teammate_assignment_info.get(owner)
        if not assignment:
            return False
        task = load_task(str(assignment["task_id"]))
        if task.status != "completed" or task.owner != owner:
            return False
        teammate_assignment_info.pop(owner, None)
        advance_assignment_version(owner)
        if owner in globals().get("plan_gates", {}):
            globals()["plan_gates"][owner] = "not_required"
        return True

def advance_assignment_version(owner: str):
    """Invalidate old approvals without clearing an explicit plan requirement."""
    with task_lock:
        assignment_versions[owner] = assignment_versions.get(owner, 0) + 1
        gates = globals().get("plan_gates")
        request_ids = globals().get("plan_request_ids")
        team = globals().get("team_lock")
        if team is not None:
            team.acquire()
        try:
            if (isinstance(gates, dict) and owner in gates
                    and gates[owner] != "not_required"):
                gates[owner] = "required"
            if isinstance(request_ids, dict):
                request_ids.pop(owner, None)
        finally:
            if team is not None:
                team.release()

def apply_shutdown_request(name: str, msg: dict) -> tuple[bool, str]:
    """Accept only a pending shutdown request sent by Lead to this teammate."""
    request_id = msg.get("metadata", {}).get("request_id", "")
    with team_lock:
        state = pending_requests.get(request_id)
        valid = (
            msg.get("from") == "lead"
            and msg.get("to") == name
            and state is not None
            and state.type == "shutdown"
            and state.sender == "lead"
            and state.target == name
            and state.status == "pending"
            and active_teammates.get(name) != "stopping"
        )
        if not valid:
            return False, "[Ignored shutdown request: request mismatch]"
        active_teammates[name] = "stopping"
    return True, request_id

def apply_plan_response(name: str, msg: dict) -> tuple[bool, str]:
    """Apply only the Lead response for this teammate's current plan."""
    metadata = msg.get("metadata", {})
    request_id = metadata.get("request_id", "")
    work_version, task_id = current_work_identity(name)
    with team_lock:
        state = pending_requests.get(request_id)
        expected_id = plan_request_ids.get(name)
        valid = (
            msg.get("from") == "lead"
            and msg.get("to") == name
            and request_id == expected_id
            and state is not None
            and state.type == "plan_approval"
            and state.sender == name
            and state.target == "lead"
            and state.work_version == work_version
            and state.task_id == task_id
            and state.status in {"approved", "rejected"}
            and metadata.get("approve", False)
            == (state.status == "approved")
        )
        if not valid:
            return False, "[Ignored plan response: request mismatch]"
        plan_gates[name] = state.status
        active_teammates[name] = "working"
        plan_request_ids.pop(name, None)
        outcome = state.status
    return True, f"[Plan {outcome}] {msg['content']}"


def _run_teammate_tool(name: str, block, handlers: dict) -> str:
    gate = plan_gates.get(name, "not_required")
    if block.name in {"bash", "write_file", "edit_file"}:
        if gate != "approved":
            if gate != "not_required":
                return (f"Blocked: plan status is {gate}. Submit or revise the "
                        "plan and wait for approval before changing the workspace.")
        blocked = check_permission(block, prompt_user=False)
        if blocked:
            return blocked
    handler = handlers.get(block.name)
    if not handler:
        return f"Unknown tool: {block.name}"
    trigger_hook("PreToolUse", block, skip_permission=True)
    try:
        output = str(handler(**block.input))
    except Exception as exc:
        output = f"Error: {type(exc).__name__}: {exc}"
    trigger_hook("PostToolUse", block, output)
    return output

def _last_assistant_text(content) -> str:
    for block in content:
        if getattr(block, "type", None) == "text":
            return block.text.strip()
        if isinstance(block, dict) and block.get("type") == "text":
            return str(block.get("text", "")).strip()
    return ""

def current_work_identity(owner: str) -> tuple[int, str | None]:
    with task_lock:
        assignment = teammate_assignment_info.get(owner)
        task_id = str(assignment["task_id"]) if assignment else None
        return assignment_versions.get(owner, 0), task_id

def release_teammate_assignment(owner: str):
    """Return abandoned teammate work to the task board on thread exit."""
    with task_lock:
        try:
            task = owner_in_progress(owner)
            if task:
                task.status = "pending"
                task.owner = None
                save_task(task)
        finally:
            teammate_assignment_info.pop(owner, None)
            advance_assignment_version(owner)
            if owner in globals().get("plan_gates", {}):
                globals()["plan_gates"][owner] = "not_required"

def claim_next_task(name: str) -> Task | None:
    """Claim the first still-available task, never a second assignment."""
    with task_lock:
        if teammate_assignment_info.get(name) or owner_in_progress(name):
            return None
    for task in scan_unclaimed_tasks():
        result = claim_task(task.id, owner=name)
        if result.startswith("Claimed "):
            return load_task(task.id)
    return None

def scan_unclaimed_tasks() -> list[Task]:
    """Return ready tasks whose optional worktree binding is usable."""
    with task_lock:
        ready = []
        for task in list_tasks():
            if (task.status != "pending" or task.owner is not None
                    or not (task.id)):
                continue
            _, error = task_worktree_cwd(task)
            if not error:
                ready.append(task)
        return ready


def check_permission(block, prompt_user: bool = True) -> str | None:
    if block.name == "bash":
        command = block.input.get("command", "")
        for pattern in DENY_LIST:
            if pattern in command:
                return f"Permission denied by deny list: {pattern}"
        if any(keyword in command for keyword in DESTRUCTIVE):
            if not prompt_user:
                return "Permission required: ask Lead to run this command."
            print(f"\n[permission] {block.name}({block.input})")
            if input("Allow? [y/N] ").strip().lower() not in {"y", "yes"}:
                return "Permission denied by user"

    if block.name in {"read_file", "write_file", "edit_file"}:
        raw_path = block.input.get("path", "")
        if not (WORKDIR / raw_path).resolve().is_relative_to(WORKDIR.resolve()):
            if not prompt_user:
                return "Permission required: path is outside the workspace."
            print(f"\n[permission] {block.name}({block.input})")
            if input("Allow? [y/N] ").strip().lower() not in {"y", "yes"}:
                return "Permission denied by user"

import json
import threading
from pathlib import Path
from types import SimpleNamespace

import pytest

from bareloop.agent_team import config, index
from bareloop.tools.adapters import teamate


@pytest.fixture(autouse=True)
def clean_team_state(monkeypatch, tmp_path):
    monkeypatch.setattr(index, "MAIL_ROOT", tmp_path / "mail")
    monkeypatch.setattr(index, "MAILBOX_ROOT", (tmp_path / "mail").resolve())
    index.active_teammates.clear()
    index.plan_gates.clear()
    index.plan_request_ids.clear()
    index.assignment_versions.clear()
    index.teammate_threads.clear()
    index.pending_requests.clear()
    index.teammate_assignment_info.clear()
    yield
    index.active_teammates.clear()
    index.plan_gates.clear()
    index.plan_request_ids.clear()
    index.assignment_versions.clear()
    index.teammate_threads.clear()
    index.pending_requests.clear()
    index.teammate_assignment_info.clear()


def test_message_bus_wait_is_reentrant_and_decodes_json_lines():
    bus = index.MessageBus()
    bus.send("lead", "worker", "hello")
    result = []

    thread = threading.Thread(
        target=lambda: result.extend(bus.wait_for_messages("worker", timeout=0.05)),
        daemon=True,
    )
    thread.start()
    thread.join(timeout=0.5)

    assert not thread.is_alive(), "wait_for_messages deadlocked while holding its condition"
    assert result == [
        {
            "from": "lead",
            "to": "worker",
            "content": "hello",
            "type": "message",
            "metadata": {},
        }
    ]


def test_teammate_tools_are_openai_schemas_and_match_runtime_handlers():
    runtime = index.TeammateRuntime("worker", "developer", "fix it", None, False)

    schemas = config.TEAMMATE_TOOLS
    assert all(schema["type"] == "function" for schema in schemas)
    names = {schema["function"]["name"] for schema in schemas}
    assert names == set(runtime.handlers)
    assert {"bash", "read", "write", "edit", "glob"} <= names
    assert "read_file" not in names
    assert "write_file" not in names
    assert "edit_file" not in names
    for schema in schemas:
        assert "cwd" not in schema["function"]["parameters"]["properties"]
    bash_schema = next(schema for schema in schemas if schema["function"]["name"] == "bash")
    assert set(bash_schema["function"]["parameters"]["properties"]) == {"command"}


def test_runtime_uses_system_message_and_keeps_work_request_separate():
    runtime = index.TeammateRuntime("worker", "developer", "fix it", None, False)

    assert runtime.messages == [
        {"role": "system", "content": runtime.system},
        {"role": "user", "content": "fix it"},
    ]
    assert not runtime.system.startswith('"')


def test_no_task_runtime_has_handlers_and_cwd_tools_reject_safely():
    runtime = index.TeammateRuntime("worker", "developer", "wait", None, False)

    assert runtime.bash("pwd") == "Error: 需要先分配task才能调用工具"
    assert runtime.read("README.md") == "Error: 需要先分配task才能调用工具"
    assert runtime.write("x.txt", "x") == "Error: 需要先分配task才能调用工具"
    assert runtime.edit("x.txt", "x", "y") == "Error: 需要先分配task才能调用工具"
    assert runtime.glob("*.py") == "Error: 需要先分配task才能调用工具"


def test_cwd_tool_handlers_forward_all_parameters(monkeypatch, tmp_path):
    runtime = index.TeammateRuntime("worker", "developer", "work", None, False)
    monkeypatch.setattr(runtime, "current_cwd", lambda: (tmp_path, None))
    calls = []
    monkeypatch.setattr(
        index,
        "run_bash",
        lambda command, cwd=None, shouldBack=False: (
            calls.append(("bash", command, cwd, shouldBack)) or "bash-ok"
        ),
    )
    monkeypatch.setattr(
        index,
        "run_read",
        lambda path, limit=None, cwd=None: calls.append(("read", path, limit, cwd)) or "read-ok",
    )
    monkeypatch.setattr(
        index,
        "run_write",
        lambda path, content, cwd=None: calls.append(("write", path, content, cwd)) or "write-ok",
    )
    monkeypatch.setattr(
        index,
        "run_edit",
        lambda path, old_text, new_text, cwd=None: (
            calls.append(("edit", path, old_text, new_text, cwd)) or "edit-ok"
        ),
    )
    monkeypatch.setattr(
        index,
        "run_glob",
        lambda pattern, cwd=None: calls.append(("glob", pattern, cwd)) or "glob-ok",
    )

    assert runtime.bash("make", shouldBack=True) == "bash-ok"
    assert runtime.read("a", limit=4) == "read-ok"
    assert runtime.write("b", "body") == "write-ok"
    assert runtime.edit("c", "old", "new") == "edit-ok"
    assert runtime.glob("**/*.py") == "glob-ok"
    assert calls == [
        ("bash", "make", tmp_path, True),
        ("read", "a", 4, tmp_path),
        ("write", "b", "body", tmp_path),
        ("edit", "c", "old", "new", tmp_path),
        ("glob", "**/*.py", tmp_path),
    ]


def test_work_uses_openai_chat_completions_and_tool_messages(monkeypatch):
    tool_call = SimpleNamespace(
        id="call_1",
        type="function",
        function=SimpleNamespace(name="list_tasks", arguments=json.dumps({})),
    )
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    captured = {}

    def create(**kwargs):
        captured.update(kwargs)
        captured["messages"] = list(kwargs["messages"])
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    fake_client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    monkeypatch.setattr(index, "client", fake_client)
    monkeypatch.setattr(index.BUS, "read_inbox", lambda _name: [])
    monkeypatch.setattr(index, "_run_teammate_tool", lambda *_args: "No tasks")
    runtime = index.TeammateRuntime("worker", "developer", "inspect", None, False)
    initial_messages = list(runtime.messages)

    assert runtime.work() == "continue"
    assert captured == {
        "model": index.PRIMARY_MODEL,
        "messages": initial_messages,
        "tools": config.TEAMMATE_TOOLS,
    }
    assert runtime.messages[len(initial_messages)] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {
                "id": "call_1",
                "type": "function",
                "function": {"name": "list_tasks", "arguments": "{}"},
            }
        ],
    }
    assert runtime.messages[len(initial_messages) + 1] == {
        "role": "tool",
        "tool_call_id": "call_1",
        "content": "No tasks",
    }


def test_tool_call_arguments_are_json_decoded_and_plan_gate_uses_registry_names(monkeypatch):
    block = SimpleNamespace(
        id="call_1",
        function=SimpleNamespace(name="write", arguments='{"path":"x","content":"y"}'),
    )
    index.plan_gates["worker"] = "required"
    called = []

    blocked = index._run_teammate_tool(
        "worker", block, {"write": lambda **kwargs: called.append(kwargs)}
    )
    assert blocked.startswith("Blocked: plan status is required")
    assert called == []

    index.plan_gates["worker"] = "approved"
    monkeypatch.setattr(index, "trigger_hook", lambda *_args, **_kwargs: None)
    assert (
        index._run_teammate_tool("worker", block, {"write": lambda **kwargs: kwargs["content"]})
        == "y"
    )


class FakeThread:
    created = []

    def __init__(self, *, target, name, daemon):
        self.target = target
        self.name = name
        self.daemon = daemon
        self.started = False
        self.created.append(self)

    def start(self):
        self.started = True


def test_spawn_creates_registers_and_starts_daemon_thread(monkeypatch):
    runtime = SimpleNamespace(run=lambda: None)
    monkeypatch.setattr(index, "TeammateRuntime", lambda *args: runtime)
    FakeThread.created.clear()
    monkeypatch.setattr(index.threading, "Thread", FakeThread)

    result = index.spawn_teammate_thread("worker", "developer", "prompt")

    thread = FakeThread.created[0]
    assert result == "Spawned teammate 'worker'"
    assert thread.daemon is True
    assert thread.target == runtime.run
    assert thread.started is True
    assert index.teammate_threads["worker"] is thread
    assert index.active_teammates["worker"] == "working"
    assert index.assignment_versions["worker"] == 0


def test_spawn_rolls_back_all_state_when_runtime_start_fails(monkeypatch):
    monkeypatch.setattr(index, "TeammateRuntime", lambda *args: SimpleNamespace(run=lambda: None))

    class BrokenThread(FakeThread):
        def start(self):
            raise RuntimeError("cannot start")

    monkeypatch.setattr(index.threading, "Thread", BrokenThread)
    released = []
    monkeypatch.setattr(index, "release_teammate_assignment", released.append)

    result = index.spawn_teammate_thread("worker", "developer", "prompt")

    assert result == "Error: failed to spawn teammate 'worker': cannot start"
    assert "worker" not in index.active_teammates
    assert "worker" not in index.plan_gates
    assert "worker" not in index.assignment_versions
    assert "worker" not in index.teammate_threads
    assert released == []


def test_runtime_error_cleans_unconsumable_shutdown_request(monkeypatch):
    request_id = "req_shutdown"
    index.active_teammates["worker"] = "working"
    index.pending_requests[request_id] = index.ProtocolState(
        request_id=request_id,
        type="shutdown",
        sender="lead",
        target="worker",
        status="pending",
        payload="",
    )
    runtime = index.TeammateRuntime("worker", "developer", "work", None, False)
    monkeypatch.setattr(runtime, "work", lambda: (_ for _ in ()).throw(RuntimeError("boom")))
    monkeypatch.setattr(index, "release_teammate_assignment", lambda _name: None)
    monkeypatch.setattr(index.BUS, "send", lambda *_args: None)

    runtime.run()

    assert request_id not in index.pending_requests


def test_plan_submission_uses_owner_assignment_version(monkeypatch):
    index.active_teammates["worker"] = "working"
    index.plan_gates["worker"] = "required"
    index.assignment_versions["worker"] = 7
    index.teammate_assignment_info["worker"] = {"task_id": "task_12345678", "cwd": Path.cwd()}
    sent = []
    monkeypatch.setattr(index.BUS, "send", lambda *args: sent.append(args))
    monkeypatch.setattr(index, "new_request_id", lambda: "req_000001")

    result = index._teammate_submit_plan("worker", "Do the work")

    state = index.pending_requests["req_000001"]
    assert state.work_version == 7
    assert state.task_id == "task_12345678"
    assert index.plan_request_ids["worker"] == "req_000001"
    assert index.plan_gates["worker"] == "pending"
    assert "req_000001" in result
    assert sent[0][3] == "plan_approval_request"


def test_adapter_signatures_match_registry_and_shutdown_calls_request_id(monkeypatch):
    index.active_teammates["worker"] = "idle"
    sent = []
    monkeypatch.setattr(teamate.BUS, "send", lambda *args: sent.append(args))
    monkeypatch.setattr(teamate, "new_request_id", lambda: "req_000009")

    result = teamate.run_request_shutdown(teammate="worker")

    assert "req_000009" in result
    assert index.pending_requests["req_000009"].target == "worker"
    assert sent[0][3] == "shutdown_request"
    assert sent[0][4] == {"request_id": "req_000009"}


def test_list_teammates_and_request_plan_use_registry_parameter_names(monkeypatch):
    index.active_teammates.update({"alpha": "working", "beta": "idle"})
    sent = []
    monkeypatch.setattr(teamate.BUS, "send", lambda *args: sent.append(args))

    assert teamate.run_list_teammates() == "alpha: working\nbeta: idle"
    assert "alpha" in teamate.run_request_plan(teammate="alpha", task="Draft first")
    assert index.plan_gates["alpha"] == "required"
    assert sent[0][3] == "plan_request"


def test_request_plan_does_not_replace_a_pending_review(monkeypatch):
    index.active_teammates["worker"] = "waiting_approval"
    index.plan_gates["worker"] = "pending"
    index.plan_request_ids["worker"] = "req_current"
    sent = []
    monkeypatch.setattr(teamate.BUS, "send", lambda *args: sent.append(args))

    result = teamate.run_request_plan(teammate="worker", task="try again")

    assert result == "A plan is already waiting for review."
    assert index.plan_gates["worker"] == "pending"
    assert index.plan_request_ids["worker"] == "req_current"
    assert sent == []


@pytest.mark.parametrize("approve,status", [(True, "approved"), (False, "rejected")])
def test_review_plan_sends_decision_and_response_application_cleans_request(
    monkeypatch, approve, status
):
    request_id = "req_000123"
    index.active_teammates["worker"] = "waiting_approval"
    index.plan_gates["worker"] = "pending"
    index.plan_request_ids["worker"] = request_id
    index.assignment_versions["worker"] = 2
    index.pending_requests[request_id] = index.ProtocolState(
        request_id=request_id,
        type="plan_approval",
        sender="worker",
        target="lead",
        status="pending",
        payload="plan",
        work_version=2,
        task_id=None,
    )
    sent = []
    monkeypatch.setattr(teamate.BUS, "send", lambda *args: sent.append(args))

    result = teamate.run_review_plan(request_id=request_id, approve=approve, feedback="looks good")
    assert status in result.lower()
    assert index.pending_requests[request_id].status == status
    assert sent[0][0:4] == ("lead", "worker", "looks good", "plan_approval_response")
    assert sent[0][4] == {"request_id": request_id, "approve": approve}

    accepted, _ = index.apply_plan_response(
        "worker",
        {
            "from": "lead",
            "to": "worker",
            "content": "looks good",
            "type": "plan_approval_response",
            "metadata": {"request_id": request_id, "approve": approve},
        },
    )
    assert accepted is True
    assert request_id not in index.pending_requests


def test_review_plan_rejects_a_stale_request(monkeypatch):
    index.active_teammates["worker"] = "waiting_approval"
    index.plan_request_ids["worker"] = "req_current"
    index.pending_requests["req_stale"] = index.ProtocolState(
        request_id="req_stale",
        type="plan_approval",
        sender="worker",
        target="lead",
        status="pending",
        payload="stale",
    )
    sent = []
    monkeypatch.setattr(teamate.BUS, "send", lambda *args: sent.append(args))

    result = teamate.run_review_plan("req_stale", approve=True)

    assert result == "Error: stale plan request req_stale"
    assert index.pending_requests["req_stale"].status == "pending"
    assert sent == []


def test_shutdown_response_matching_cleans_request():
    request_id = "req_000777"
    index.pending_requests[request_id] = index.ProtocolState(
        request_id=request_id,
        type="shutdown",
        sender="lead",
        target="worker",
        status="pending",
        payload="",
    )

    assert index.match_response("shutdown_response", request_id, True, "worker", "lead") is True
    assert request_id not in index.pending_requests


def test_advancing_assignment_version_cleans_stale_plan_request():
    request_id = "req_000888"
    index.assignment_versions["worker"] = 3
    index.plan_gates["worker"] = "pending"
    index.plan_request_ids["worker"] = request_id
    index.pending_requests[request_id] = index.ProtocolState(
        request_id=request_id,
        type="plan_approval",
        sender="worker",
        target="lead",
        status="pending",
        payload="old plan",
        work_version=3,
    )

    index.advance_assignment_version("worker")

    assert index.assignment_versions["worker"] == 4
    assert index.plan_gates["worker"] == "required"
    assert "worker" not in index.plan_request_ids
    assert request_id not in index.pending_requests


@pytest.mark.parametrize(
    ("initial_gate", "expected_gate"),
    [("approved", "required"), ("not_required", "not_required")],
)
def test_completed_assignment_preserves_plan_policy(monkeypatch, initial_gate, expected_gate):
    task = SimpleNamespace(id="task_12345678", status="completed", owner="worker")
    index.teammate_assignment_info["worker"] = {
        "task_id": task.id,
        "cwd": Path.cwd(),
    }
    index.assignment_versions["worker"] = 1
    index.plan_gates["worker"] = initial_gate
    monkeypatch.setattr(index, "load_task", lambda _task_id: task)

    assert index.release_completed_assignment("worker") is True
    assert index.plan_gates["worker"] == expected_gate
    assert index.assignment_versions["worker"] == 2

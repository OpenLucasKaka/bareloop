import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest


def test_settings_imports_without_mode_goal_cycle() -> None:
    result = subprocess.run(
        [sys.executable, "-c", "import bareloop.task_system; import bareloop.settings"],
        capture_output=True,
        text=True,
        check=False,
    )

    assert result.returncode == 0, result.stderr


def test_shell_uses_optional_cwd(tmp_path: Path) -> None:
    from bareloop.tools.shell import run_bash

    assert run_bash("pwd", cwd=tmp_path) == str(tmp_path)


def test_filesystem_uses_cwd_as_security_root(tmp_path: Path) -> None:
    from bareloop.tools.filesystem import run_glob, run_read, run_write

    assert run_write("nested/note.txt", "hello", cwd=tmp_path) == "Wrote nested/note.txt"
    assert run_read("nested/note.txt", cwd=tmp_path) == "hello"
    assert run_glob("**/*.txt", cwd=tmp_path) == "nested/note.txt"
    assert run_read("../outside.txt", cwd=tmp_path).startswith(
        "Error: path escapes working directory"
    )


def test_permission_hook_prompts_for_external_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    from bareloop.hook.hook import permission_hook

    monkeypatch.setattr("builtins.input", lambda _prompt: "n")

    result = permission_hook({"name": "read", "arguments": {"path": "hosts", "cwd": "/etc"}})

    assert result == "user denied"


def test_permission_hook_hard_denies_blocklisted_command(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bareloop.hook.hook import permission_hook

    def unexpected_prompt(_prompt: str) -> str:
        raise AssertionError("deny-listed commands must not prompt")

    monkeypatch.setattr("builtins.input", unexpected_prompt)

    result = permission_hook(
        {"name": "bash", "arguments": {"command": "sudo reboot", "cwd": "/tmp"}}
    )

    assert result == "Permission denied: reboot"


def test_builtin_tool_schemas_expose_optional_cwd() -> None:
    from bareloop.tools.registry import get_tool

    for name in ("bash", "read", "write", "edit", "glob"):
        definition = get_tool(name)
        assert definition is not None
        assert "cwd" in definition.parameters["properties"]
        assert "cwd" not in definition.parameters["required"]


def test_cli_wait_wakes_while_prompt_is_pending(monkeypatch: pytest.MonkeyPatch) -> None:
    from bareloop import mian

    never_finishes = asyncio.Event()

    async def prompt_async(_prompt: str, **_kwargs) -> str:
        await never_finishes.wait()
        return "unreachable"

    monkeypatch.setattr(mian.PROMPT_SESSION, "prompt_async", prompt_async)
    monkeypatch.setattr(mian.BUS, "peek", lambda _recipient: ["event"])

    result = asyncio.run(asyncio.wait_for(mian.wait_for_cli_event(), timeout=0.2))

    assert result == ("wake", None, mian.AgentMode.NORMAL)


def test_cli_mode_prompts_keep_input_label_fixed(monkeypatch: pytest.MonkeyPatch) -> None:
    from bareloop import mian

    prompts = []

    async def prompt_async(prompt: str, **options) -> str:
        prompts.append((prompt, options["bottom_toolbar"]))
        return "quit"

    monkeypatch.setattr(mian.PROMPT_SESSION, "prompt_async", prompt_async)
    monkeypatch.setattr(mian.BUS, "peek", lambda _recipient: [])

    asyncio.run(mian.wait_for_cli_event(mian.AgentMode.NORMAL))
    asyncio.run(mian.wait_for_cli_event(mian.AgentMode.GOAL))

    assert prompts == [
        (
            "› ",
            [
                ("class:hint", "  /mode 切换"),
                ("class:hint", f" · {mian.WORKDIR}"),
            ],
        ),
        (
            "› ",
            [
                ("class:mode.goal", "  Goal mode · /mode 切换"),
                ("class:hint", f" · {mian.WORKDIR}"),
            ],
        ),
    ]


def test_pre_user_prompt_hook_does_not_print_cwd(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    import importlib

    hook_module = importlib.import_module("bareloop.hook.hook")
    monkeypatch.setattr(
        hook_module,
        "HOOKS",
        {event: [] for event in hook_module.HOOKS},
    )

    hook_module.hook()
    hook_module.trigger_hook("PreUserPromptInput", "test input")

    assert capsys.readouterr().out == ""


def test_cli_prompt_uses_gray_placeholder(monkeypatch: pytest.MonkeyPatch) -> None:
    from bareloop import mian

    prompt_options = {}

    async def prompt_async(_prompt: str, **options) -> str:
        prompt_options.update(options)
        return "quit"

    monkeypatch.setattr(mian.PROMPT_SESSION, "prompt_async", prompt_async)
    monkeypatch.setattr(mian.BUS, "peek", lambda _recipient: [])

    asyncio.run(mian.wait_for_cli_event(mian.AgentMode.NORMAL))

    assert prompt_options["placeholder"] == [("class:placeholder", "请输入内容")]
    placeholder_style = mian.CLI_STYLE.get_attrs_for_style_str("class:placeholder")
    assert placeholder_style.color == "ansibrightblack"


def test_create_session_starts_with_configured_default_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bareloop import mian

    received_modes = []

    async def fake_mcp_init() -> None:
        return None

    async def fake_wait_for_cli_event(selected_mode):
        received_modes.append(selected_mode)
        return "quit", None, selected_mode

    class FakeTraceWriter:
        def write(self, **_kwargs) -> None:
            return None

    monkeypatch.setattr(mian, "init_hooks", lambda: None)
    monkeypatch.setattr(mian, "mcp_init", fake_mcp_init)
    monkeypatch.setattr(mian, "_scan_skills", lambda: None)
    monkeypatch.setattr(mian, "TraceWriter", FakeTraceWriter)
    monkeypatch.setattr(mian, "start_cron_scheduler", lambda *_args: None)
    monkeypatch.setattr(mian, "wait_for_cli_event", fake_wait_for_cli_event)

    mian.create_session()

    assert received_modes == [mian.DEFAUlT_MODEL]


def test_cli_mode_command_can_switch_back_to_normal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bareloop import mian

    class FakeDialog:
        async def run_async(self):
            return mian.AgentMode.NORMAL

    async def prompt_async(_prompt: str, **_kwargs) -> str:
        return "/mode"

    monkeypatch.setattr(mian.PROMPT_SESSION, "prompt_async", prompt_async)
    monkeypatch.setattr(mian, "radiolist_dialog", lambda **_kwargs: FakeDialog())

    result = asyncio.run(mian.wait_for_cli_event(mian.AgentMode.GOAL))

    assert result == ("next", None, mian.AgentMode.NORMAL)


def test_cli_quit_result_preserves_current_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    from bareloop import mian

    async def prompt_async(_prompt: str, **_kwargs) -> str:
        return "quit"

    monkeypatch.setattr(mian.PROMPT_SESSION, "prompt_async", prompt_async)

    result = asyncio.run(mian.wait_for_cli_event(mian.AgentMode.GOAL))

    assert result == ("quit", None, mian.AgentMode.GOAL)


def test_queue_processor_passes_shared_session_and_releases_lock(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import bareloop.mian as mian
    from bareloop.cron_scheduler import index as cron

    messages = [{"role": "system", "content": "system"}]
    trace = object()
    calls = []

    class StopAfterOne:
        calls = 0

        def wait(self, _timeout: float) -> bool:
            self.calls += 1
            return self.calls > 1

    class Lock:
        released = False

        def acquire(self, blocking: bool = True) -> bool:
            assert blocking is False
            return True

        def release(self) -> None:
            self.released = True

    lock = Lock()
    monkeypatch.setattr(cron, "has_cron_queue", lambda: True)
    monkeypatch.setattr(cron, "agent_lock", lock)
    monkeypatch.setattr(
        mian,
        "run_agent_turn_locked",
        lambda received_messages, received_trace: calls.append((received_messages, received_trace)),
    )

    cron.queue_processor_loop(messages, trace, StopAfterOne())

    assert calls == [(messages, trace)]
    assert lock.released is True


def test_cron_step_expression_matches() -> None:
    from datetime import datetime

    from bareloop.cron_scheduler.index import cron_matches

    assert cron_matches("*/5 * * * *", datetime(2026, 9, 4, 12, 10)) is True


def test_cron_month_field_is_enforced() -> None:
    from datetime import datetime

    from bareloop.cron_scheduler.index import cron_matches

    assert cron_matches("* * * 10 *", datetime(2026, 9, 4, 12, 10)) is False


def test_cron_day_and_weekday_use_or_semantics() -> None:
    from datetime import datetime

    from bareloop.cron_scheduler.index import cron_matches

    assert cron_matches("* * 1 * 5", datetime(2026, 9, 4, 12, 0)) is True


def test_cron_list_can_include_ranges() -> None:
    from datetime import datetime

    from bareloop.cron_scheduler.index import cron_matches

    assert cron_matches("1-3,5 * * * *", datetime(2026, 9, 4, 12, 5)) is True


def test_schedule_cron_creates_storage_parent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.cron_scheduler import index as cron

    monkeypatch.setattr(cron, "DURABLE_CRON_PATH", tmp_path / "nested" / "cron.json")
    monkeypatch.setattr(cron, "scheduled_jobs", {})

    job = cron.schedule_cron("0 12 * * *", "run checks", recurring=True)

    assert isinstance(job, cron.CronJob)
    assert cron.DURABLE_CRON_PATH.is_file()


def test_cancel_cron_persists_removal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from bareloop.cron_scheduler import index as cron

    path = tmp_path / "cron.json"
    job = cron.CronJob("cron_deadbeef", "0 12 * * *", "run checks", recurring=True)
    monkeypatch.setattr(cron, "DURABLE_CRON_PATH", path)
    monkeypatch.setattr(cron, "scheduled_jobs", {job.id: job})
    monkeypatch.setattr(cron, "cron_queue", [job])

    assert cron.cancel_job(job.id) == f"Cancelled {job.id}"
    assert path.read_text(encoding="utf-8") == "[]"


def test_agent_loop_acknowledges_delivered_cron(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from bareloop import loop

    job = SimpleNamespace(id="cron_deadbeef", prompt="run checks")
    acknowledged = []
    restored = []
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))]
    )
    monkeypatch.setattr(loop, "consume_cron_queue", lambda: [job])
    monkeypatch.setattr(loop, "acknowledge_cron_jobs", acknowledged.extend, raising=False)
    monkeypatch.setattr(loop, "restore_cron_jobs", restored.extend, raising=False)
    monkeypatch.setattr(loop, "load_memories", lambda _messages: "")
    monkeypatch.setattr(loop, "extract_memories", lambda *_args: None)
    monkeypatch.setattr(loop, "consolidate_memories", lambda: None)
    monkeypatch.setattr(loop, "inject_background_results", lambda _messages: None)
    monkeypatch.setattr(loop, "tool_budget_result", lambda messages: messages)
    monkeypatch.setattr(loop, "micro_compact", lambda messages: messages)
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "trigger_hook", lambda *_args: None)
    monkeypatch.setattr(
        loop,
        "tokenizer",
        SimpleNamespace(apply_chat_template=lambda *_args, **_kwargs: []),
    )
    monkeypatch.setattr(
        loop.client,
        "chat",
        SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response)),
    )

    loop.agent_loop(
        [{"role": "system", "content": "system"}], SimpleNamespace(write=lambda **_: None)
    )

    assert acknowledged == [job]
    assert restored == []


def test_cron_only_turn_does_not_extract_previous_assistant_as_evidence(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from bareloop import loop

    job = SimpleNamespace(id="cron_deadbeef", prompt="run checks")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))]
    )
    extracted = []
    monkeypatch.setattr(loop, "consume_cron_queue", lambda: [job])
    monkeypatch.setattr(loop, "acknowledge_cron_jobs", lambda _jobs: None)
    monkeypatch.setattr(loop, "load_memories", lambda _messages: "")
    monkeypatch.setattr(loop, "extract_memories", lambda *args: extracted.append(args))
    monkeypatch.setattr(loop, "consolidate_memories", lambda: None)
    monkeypatch.setattr(loop, "inject_background_results", lambda _messages: None)
    monkeypatch.setattr(loop, "tool_budget_result", lambda messages: messages)
    monkeypatch.setattr(loop, "micro_compact", lambda messages: messages)
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "trigger_hook", lambda *_args: None)
    monkeypatch.setattr(
        loop,
        "tokenizer",
        SimpleNamespace(apply_chat_template=lambda *_args, **_kwargs: []),
    )
    monkeypatch.setattr(
        loop.client,
        "chat",
        SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response)),
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "assistant", "content": "previous answer"},
    ]

    loop.agent_loop(messages, SimpleNamespace(write=lambda **_: None))

    assert extracted == [
        (
            [
                {"role": "user", "content": "[Scheduled] run checks"},
                {"role": "assistant", "content": "done"},
            ],
            0,
        )
    ]


def test_agent_loop_restores_cron_after_model_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from bareloop import loop

    job = SimpleNamespace(id="cron_deadbeef", prompt="run checks")
    acknowledged = []
    restored = []
    monkeypatch.setattr(loop, "consume_cron_queue", lambda: [job])
    monkeypatch.setattr(loop, "acknowledge_cron_jobs", acknowledged.extend, raising=False)
    monkeypatch.setattr(loop, "restore_cron_jobs", restored.extend, raising=False)
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

    def fail(**_kwargs):
        raise RuntimeError("provider unavailable")

    monkeypatch.setattr(
        loop.client,
        "chat",
        SimpleNamespace(completions=SimpleNamespace(create=fail)),
    )

    messages = [{"role": "system", "content": "system"}]
    loop.agent_loop(messages, SimpleNamespace(write=lambda **_: None))

    assert acknowledged == []
    assert restored == [job]
    assert messages == [{"role": "system", "content": "system"}]


def test_agent_loop_does_not_replay_cron_after_tool_execution(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from bareloop import loop

    job = SimpleNamespace(id="cron_deadbeef", prompt="run checks")
    tool_call = SimpleNamespace(
        id="call_one",
        function=SimpleNamespace(name="bash", arguments='{"command":"true"}'),
    )
    first_response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call]))]
    )
    calls = 0

    def create(**_kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            return first_response
        raise RuntimeError("provider unavailable")

    acknowledged = []
    restored = []
    monkeypatch.setattr(loop, "consume_cron_queue", lambda: [job])
    monkeypatch.setattr(loop, "acknowledge_cron_jobs", acknowledged.extend)
    monkeypatch.setattr(loop, "restore_cron_jobs", restored.extend)
    monkeypatch.setattr(loop, "load_memories", lambda _messages: "")
    monkeypatch.setattr(loop, "inject_background_results", lambda _messages: None)
    monkeypatch.setattr(loop, "tool_budget_result", lambda messages: messages)
    monkeypatch.setattr(loop, "micro_compact", lambda messages: messages)
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "trigger_hook", lambda *_args: None)
    monkeypatch.setattr(
        loop,
        "normalize_tool_call",
        lambda _tool: {"id": "call_one", "name": "bash", "arguments": {"command": "true"}},
    )
    monkeypatch.setattr(loop, "dispatch_tool", lambda *_args, **_kwargs: "ok")
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
    messages = [{"role": "system", "content": "system"}]

    loop.agent_loop(messages, SimpleNamespace(write=lambda **_: None))

    assert acknowledged == [job]
    assert restored == []
    assert any(message.get("content") == "[Scheduled] run checks" for message in messages)


def test_agent_loop_extracts_memories_from_turn_buffer_after_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from bareloop import loop

    tool_call = SimpleNamespace(
        id="call_one",
        function=SimpleNamespace(name="bash", arguments='{"command":"true"}'),
    )
    responses = iter(
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="", tool_calls=[tool_call]))
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(message=SimpleNamespace(content="finished", tool_calls=None))
                ]
            ),
        ]
    )
    extracted = []
    compact_calls = []
    monkeypatch.setattr(loop, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(loop, "load_memories", lambda _messages: "")
    monkeypatch.setattr(loop, "inject_background_results", lambda _messages: None)
    monkeypatch.setattr(loop, "tool_budget_result", lambda messages: messages)
    monkeypatch.setattr(loop, "micro_compact", lambda messages: messages)
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "trigger_hook", lambda *_args: None)
    monkeypatch.setattr(loop, "dispatch_tool", lambda *_args, **_kwargs: "tool output")
    monkeypatch.setattr(loop, "consolidate_memories", lambda: None)
    monkeypatch.setattr(
        loop,
        "extract_memories",
        lambda messages, count: extracted.append((messages, count)),
    )
    monkeypatch.setattr(
        loop,
        "compact_history",
        lambda messages: (
            compact_calls.append(messages.copy())
            or [
                {"role": "system", "content": "system"},
                {"role": "user", "content": "[Compacted] summary"},
            ]
        ),
    )
    monkeypatch.setattr(
        loop,
        "tokenizer",
        SimpleNamespace(
            apply_chat_template=lambda *_args, **_kwargs: range(loop.CONTEXT_LIMIT + 1)
        ),
    )
    monkeypatch.setattr(
        loop.client,
        "chat",
        SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: next(responses))),
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "original current request"},
    ]

    loop.agent_loop(
        messages,
        SimpleNamespace(write=lambda **_: None),
        loop.AgentMode.NORMAL,
    )

    assert len(compact_calls) == 2
    assert all(message.get("content") != "original current request" for message in messages)
    turn_messages, count = extracted[0]
    assert turn_messages is not messages
    assert count == 0
    assert turn_messages == [
        {"role": "user", "content": "original current request"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_one",
                    "type": "function",
                    "function": {"name": "bash", "arguments": '{"command":"true"}'},
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call_one", "content": "tool output"},
        {"role": "assistant", "content": "finished"},
    ]


def test_agent_loop_reinjects_relevant_memories_after_compaction(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from copy import deepcopy
    from types import SimpleNamespace

    from bareloop import loop

    requests = []
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content="done", tool_calls=None))]
    )
    monkeypatch.setattr(loop, "consume_cron_queue", lambda: [])
    monkeypatch.setattr(loop, "load_memories", lambda _messages: "selected durable memory")
    monkeypatch.setattr(loop, "inject_background_results", lambda _messages: None)
    monkeypatch.setattr(loop, "tool_budget_result", lambda messages: messages)
    monkeypatch.setattr(loop, "micro_compact", lambda messages: messages)
    monkeypatch.setattr(loop, "get_tool_schemas", lambda: [])
    monkeypatch.setattr(loop, "trigger_hook", lambda *_args: None)
    monkeypatch.setattr(loop, "extract_memories", lambda *_args: None)
    monkeypatch.setattr(loop, "consolidate_memories", lambda: None)
    monkeypatch.setattr(
        loop,
        "compact_history",
        lambda _messages: [
            {"role": "system", "content": "system"},
            {"role": "user", "content": "[Compacted] summary"},
        ],
    )
    monkeypatch.setattr(
        loop,
        "tokenizer",
        SimpleNamespace(
            apply_chat_template=lambda *_args, **_kwargs: range(loop.CONTEXT_LIMIT + 1)
        ),
    )
    monkeypatch.setattr(
        loop.client,
        "chat",
        SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: requests.append(deepcopy(kwargs)) or response
            )
        ),
    )
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "old request"},
        {"role": "assistant", "content": "old answer"},
        {"role": "user", "content": "current request"},
    ]

    loop.agent_loop(messages, SimpleNamespace(write=lambda **_: None))

    request_messages = requests[0]["messages"]
    assert "selected durable memory" in request_messages[-1]["content"]
    assert "[Compacted] summary" in request_messages[-1]["content"]


def test_background_shell_preserves_requested_cwd(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import threading

    from bareloop.background_system import index as background

    called = threading.Event()
    captured = {}

    def fake_run(command, cwd=None):
        captured.update(command=command, cwd=cwd)
        called.set()
        return "ok", 0

    monkeypatch.setattr(background, "run_shell_process", fake_run)
    manager = background.BackgroundManager()

    manager.start_task({"id": "call_one", "command": "pwd", "cwd": str(tmp_path)})

    assert called.wait(1)
    assert captured == {"command": "pwd", "cwd": str(tmp_path)}


def test_background_collect_does_not_drop_completion_after_unlock() -> None:
    from bareloop.background_system import index as background

    manager = background.BackgroundManager()
    manager.task = {
        "first": {"command": "one", "status": "completed"},
        "second": {"command": "two", "status": "completed"},
    }
    manager._results = {"first": "one", "second": "two"}
    manager._ready = ["first"]

    class AppendOnUnlock:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            manager._ready.append("second")

    manager.lock = AppendOnUnlock()

    manager.collect()

    assert manager._ready == ["second"]


def test_tool_budget_persists_and_rewrites_original_message(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.compact import index as compact

    monkeypatch.setattr(compact, "TOOL_RESULTS_DIR", tmp_path / "nested" / "results")
    messages = [
        {"role": "assistant", "content": "", "tool_calls": []},
        {"role": "tool", "tool_call_id": "call_one", "content": "x" * 1200},
        {"role": "tool", "tool_call_id": "call_two", "content": "y" * 1200},
    ]

    result = compact.tool_budget_result(messages, max_bytes=1500)

    assert result is messages
    assert any("<persisted-output>" in message["content"] for message in messages[1:])
    assert list((tmp_path / "nested" / "results").glob("*.txt"))
    assert sum(len(message["content"]) for message in messages[1:]) <= 1500


def test_compact_history_preserves_instruction_messages(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bareloop.compact import index as compact

    monkeypatch.setattr(compact, "write_transcript", lambda _messages: Path("trace.jsonl"))
    monkeypatch.setattr(compact, "summarize_history", lambda _messages: "summary")
    messages = [
        {"role": "system", "content": "system rules"},
        {"role": "developer", "content": "developer rules"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "answer"},
        {"role": "developer", "content": "late reminder"},
    ]

    result = compact.compact_history(messages)

    assert result[:-1] == [messages[0], messages[1], messages[4]]
    assert result[-1] == {"role": "user", "content": "[Compacted]\n\nsummary"}


def test_compact_artifacts_default_to_bareloop_state_directory() -> None:
    from bareloop.compact import index as compact

    state_root = (compact.WORKDIR / ".bareloop").resolve()

    assert compact.TRANSCRIPT_DIR.resolve().is_relative_to(state_root)
    assert compact.TOOL_RESULTS_DIR.resolve().is_relative_to(state_root)


def test_persisted_tool_output_cannot_escape_storage_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.compact import index as compact

    output_dir = tmp_path / "results"
    monkeypatch.setattr(compact, "TOOL_RESULTS_DIR", output_dir)

    compact.persist_large_output("x" * compact.PERSIST_THRESHOLD, "../../escaped")

    assert len(list(output_dir.glob("*.txt"))) == 1
    assert not (tmp_path / "escaped.txt").exists()


def test_persisted_tool_output_does_not_reuse_stale_content(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.compact import index as compact

    monkeypatch.setattr(compact, "TOOL_RESULTS_DIR", tmp_path)

    compact.persist_large_output("a" * compact.PERSIST_THRESHOLD, "call_one")
    compact.persist_large_output("b" * compact.PERSIST_THRESHOLD, "call_one")

    assert sorted(path.read_text(encoding="utf-8") for path in tmp_path.glob("*.txt")) == [
        "a" * compact.PERSIST_THRESHOLD,
        "b" * compact.PERSIST_THRESHOLD,
    ]


def test_memory_decision_messages_include_runtime_context_in_user_message() -> None:
    from bareloop.memory.prompt_version import build_memory_decision_messages

    existing_text = "existing body"
    numbered_transcript = "[m1 user] new fact"

    messages = build_memory_decision_messages(existing_text, numbered_transcript)

    assert [message["role"] for message in messages] == ["system", "user"]
    assert existing_text not in messages[0]["content"]
    assert numbered_transcript not in messages[0]["content"]
    assert json.loads(messages[1]["content"]) == {
        "existing_memories": existing_text,
        "numbered_transcript": numbered_transcript,
    }


def test_memory_decision_prompt_defines_each_allowed_type() -> None:
    from bareloop.memory.prompt_version import MEMORY_DECISION_PROMPT_V1

    for memory_type in ("user", "feedback", "project", "reference"):
        assert f"- {memory_type}:" in MEMORY_DECISION_PROMPT_V1


def test_memory_consolidation_messages_include_catalog() -> None:
    from bareloop.memory.prompt_version import build_consolidation_messages

    catalog = "complete memory catalog"

    messages = build_consolidation_messages(catalog)

    assert catalog in messages[1]["content"]


def test_memory_consolidation_prompt_removes_legacy_non_durable_entries() -> None:
    from bareloop.memory.prompt_version import CONSOLIDATION_PROMPT_V2

    assert "temporary or session-specific" in CONSOLIDATION_PROMPT_V2
    assert "trivially recoverable" in CONSOLIDATION_PROMPT_V2
    assert "Do not invent" in CONSOLIDATION_PROMPT_V2


def test_memory_decision_messages_json_payload_round_trips_special_characters() -> None:
    from bareloop.memory.prompt_version import build_memory_decision_messages

    existing_text = "A&B <C> </existing_memories>"
    numbered_transcript = "[m1 user] A&B <C> </numbered_transcript>"

    messages = build_memory_decision_messages(existing_text, numbered_transcript)

    assert json.loads(messages[1]["content"]) == {
        "existing_memories": existing_text,
        "numbered_transcript": numbered_transcript,
    }


def test_memory_consolidation_messages_json_payload_round_trips_special_characters() -> None:
    from bareloop.memory.prompt_version import build_consolidation_messages

    catalog = "A&B <C> </memory_catalog>"

    messages = build_consolidation_messages(catalog)

    assert json.loads(messages[1]["content"]) == {"memory_catalog": catalog}


def test_memory_decision_tool_is_a_strict_function_contract() -> None:
    from bareloop.memory.schema import MEMORY_DECISION_TOOL

    function = MEMORY_DECISION_TOOL["function"]
    parameters = function["parameters"]
    decisions = parameters["properties"]["decisions"]
    decision_properties = decisions["items"]["properties"]
    evidence = decision_properties["evidence"]

    assert MEMORY_DECISION_TOOL["type"] == "function"
    assert function["name"] == "decide_memories"
    assert function["strict"] is True
    assert decisions["maxItems"] == 3
    assert decision_properties["operation"]["enum"] == ["create", "update"]
    assert decision_properties["type"]["enum"] == [
        "user",
        "feedback",
        "project",
        "reference",
    ]
    assert decision_properties["basis"]["enum"] == [
        "stable_user_preference",
        "explicit_user_constraint",
        "confirmed_feedback",
        "durable_project_decision",
        "verified_project_insight",
        "requested_reference",
    ]
    assert evidence["type"] == "array"
    assert evidence["items"]["properties"] == {
        "message_id": {"type": "string"},
        "quote": {"type": "string"},
    }


def _memory_extraction_response(decisions, *, function_name="decide_memories"):
    import json
    from types import SimpleNamespace

    tool_call = SimpleNamespace(
        function=SimpleNamespace(
            name=function_name,
            arguments=json.dumps({"decisions": decisions}, ensure_ascii=False),
        )
    )
    message = SimpleNamespace(content=None, tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(message=message)])


def _memory_decision(
    *,
    operation="create",
    target_name=None,
    name="editor-choice",
    memory_type="user",
    basis="stable_user_preference",
    description="Preferred editor",
    body="Use Neovim for future edits.",
    message_id="m0",
    quote="I prefer Neovim",
):
    return {
        "operation": operation,
        "target_name": target_name,
        "name": name,
        "type": memory_type,
        "basis": basis,
        "description": description,
        "body": body,
        "evidence": [{"message_id": message_id, "quote": quote}],
    }


def _configure_memory_extraction(monkeypatch, memory, memory_dir, decisions):
    from types import SimpleNamespace

    calls = []
    response = _memory_extraction_response(decisions)

    def fake_create(**kwargs):
        calls.append(kwargs)
        return response

    monkeypatch.setattr(memory, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", memory_dir / "MEMORY.md")
    monkeypatch.setattr(
        memory,
        "client",
        SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))),
    )
    return calls


def test_memory_extraction_forces_tool_and_supplies_complete_context(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.memory import index as memory
    from bareloop.memory.schema import MEMORY_DECISION_TOOL

    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    existing_body = "完整 existing body，包含不能丢失的细节。"
    (memory_dir / "existing.md").write_text(
        f"---\nname: existing\ndescription: saved\ntype: project\n---\n\n{existing_body}\n",
        encoding="utf-8",
    )
    calls = _configure_memory_extraction(monkeypatch, memory, memory_dir, [])
    messages = [
        {"role": "system", "content": "system"},
        {"role": "user", "content": "I prefer Neovim"},
        {"role": "assistant", "content": "Checking"},
        {"role": "tool", "content": {"verified": True}},
    ]

    memory.extract_memories(messages, 1)

    request = calls[0]
    assert request["tools"] == [MEMORY_DECISION_TOOL]
    assert request["tool_choice"] == {
        "type": "function",
        "function": {"name": "decide_memories"},
    }
    assert request["parallel_tool_calls"] is False
    assert request["max_completion_tokens"] == 2000
    assert "max_tokens" not in request
    runtime_context = request["messages"][1]["content"]
    assert existing_body in runtime_context
    assert "m1" in runtime_context and "I prefer Neovim" in runtime_context
    assert "m2" in runtime_context and "Checking" in runtime_context
    assert "m3" in runtime_context and "{'verified': True}" in runtime_context


def test_memory_numbered_transcript_preserves_assistant_tool_calls() -> None:
    from bareloop.memory import index as memory

    transcript, evidence = memory._numbered_transcript(
        [
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "call_one",
                        "type": "function",
                        "function": {
                            "name": "read",
                            "arguments": '{"path":"pyproject.toml"}',
                        },
                    }
                ],
            }
        ],
        0,
    )

    assert '"name": "read"' in transcript
    assert '"arguments":' in transcript
    assert "pyproject.toml" in transcript
    assert evidence["m0"]["content"] in transcript


def test_memory_extraction_creates_valid_memory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    _configure_memory_extraction(monkeypatch, memory, memory_dir, [_memory_decision()])

    memory.extract_memories([{"role": "user", "content": "I prefer Neovim"}], 0)

    created = (memory_dir / "editor-choice.md").read_text(encoding="utf-8")
    assert "name: editor-choice" in created
    assert "type: user" in created
    assert created.endswith("Use Neovim for future edits.\n")
    assert "[editor-choice](editor-choice.md)" in (memory_dir / "MEMORY.md").read_text(
        encoding="utf-8"
    )


def test_memory_extraction_rejects_non_verbatim_html_entity_quote(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    prompt_quote = "A&amp;B &lt;C&gt;"
    decision = _memory_decision(
        name="entity-preference",
        body="Remember A&B <C>.",
        quote=prompt_quote,
    )
    calls = _configure_memory_extraction(monkeypatch, memory, memory_dir, [decision])

    memory.extract_memories([{"role": "user", "content": "A&B <C>"}], 0)

    runtime_payload = json.loads(calls[0]["messages"][1]["content"])
    assert runtime_payload["numbered_transcript"] == "[m0 user]\nA&B <C>"
    assert prompt_quote not in calls[0]["messages"][1]["content"]
    assert not memory_dir.exists()
    assert "evidence quote" in capsys.readouterr().err


def test_memory_extraction_updates_exact_memory_without_duplicate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    (memory_dir / "editor-choice.md").write_text(
        "---\nname: editor-choice\ndescription: Old\ntype: user\n---\n\nUse Vim.\n",
        encoding="utf-8",
    )
    decision = _memory_decision(
        operation="update",
        target_name="editor-choice",
        description="Updated editor preference",
    )
    _configure_memory_extraction(monkeypatch, memory, memory_dir, [decision])

    memory.extract_memories([{"role": "user", "content": "I prefer Neovim"}], 0)

    memory_files = [path for path in memory_dir.glob("*.md") if path.name != "MEMORY.md"]
    assert [path.name for path in memory_files] == ["editor-choice.md"]
    updated = memory_files[0].read_text(encoding="utf-8")
    assert "Use Neovim for future edits." in updated
    assert "Use Vim." not in updated


@pytest.mark.parametrize(
    ("message_id", "quote"),
    [("m0", "invented quote"), ("m999", "I prefer Neovim")],
)
def test_memory_extraction_rejects_invalid_evidence_without_mutation(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    message_id: str,
    quote: str,
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    existing = memory_dir / "existing.md"
    existing.write_text("original", encoding="utf-8")
    decision = _memory_decision(message_id=message_id, quote=quote)
    _configure_memory_extraction(monkeypatch, memory, memory_dir, [decision])

    memory.extract_memories([{"role": "user", "content": "I prefer Neovim"}], 0)

    assert list(memory_dir.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "original"
    assert "evidence" in capsys.readouterr().err


def test_memory_decision_validator_rejects_additional_properties() -> None:
    from copy import deepcopy

    from bareloop.memory import index as memory

    base = _memory_decision()
    payloads = []
    payloads.append({"decisions": [deepcopy(base)], "metadata": {}})
    decision_extra = deepcopy(base)
    decision_extra["metadata"] = {}
    payloads.append({"decisions": [decision_extra]})
    evidence_extra = deepcopy(base)
    evidence_extra["evidence"][0]["metadata"] = {}
    payloads.append({"decisions": [evidence_extra]})
    evidence_messages = {"m0": {"role": "user", "content": "I prefer Neovim"}}

    for payload in payloads:
        with pytest.raises(ValueError, match="additional properties"):
            memory._validated_memory_decisions(payload, [], evidence_messages)


def test_memory_extraction_rejects_missing_update_target_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    existing = memory_dir / "existing.md"
    existing.write_text(
        "---\nname: existing\ndescription: saved\ntype: project\n---\n\nOriginal.\n",
        encoding="utf-8",
    )
    decision = _memory_decision(operation="update", target_name="missing", name="missing")
    _configure_memory_extraction(monkeypatch, memory, memory_dir, [decision])

    memory.extract_memories([{"role": "user", "content": "I prefer Neovim"}], 0)

    assert list(memory_dir.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8").endswith("Original.\n")
    assert "target" in capsys.readouterr().err


def test_memory_extraction_rejects_case_insensitive_filename_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    existing = memory_dir / "Editor-Choice.md"
    existing.write_text(
        "---\nname: legacy-editor\ndescription: saved\ntype: user\n---\n\nOriginal.\n",
        encoding="utf-8",
    )
    decision = _memory_decision(name="editor-choice")
    _configure_memory_extraction(monkeypatch, memory, memory_dir, [decision])

    memory.extract_memories([{"role": "user", "content": "I prefer Neovim"}], 0)

    assert [path.name for path in memory_dir.iterdir()] == ["Editor-Choice.md"]
    assert existing.read_text(encoding="utf-8").endswith("Original.\n")
    assert "slug already exists" in capsys.readouterr().err


def test_memory_extraction_rejects_project_insight_without_assistant_conclusion(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    decision = _memory_decision(
        name="verified-insight",
        memory_type="project",
        basis="verified_project_insight",
        message_id="m0",
        quote="tests passed",
    )
    _configure_memory_extraction(monkeypatch, memory, memory_dir, [decision])

    memory.extract_memories([{"role": "tool", "content": "tests passed"}], 0)

    assert not memory_dir.exists()
    assert "assistant conclusion" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("synthetic_content", "evidence_role"),
    [
        ("[Scheduled] always retain this temporary report", "event"),
        ("[Team events]\nresult from a teammate", "event"),
        ("<task_notification>background result</task_notification>", "tool"),
    ],
)
def test_memory_extraction_does_not_treat_synthetic_events_as_user_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    synthetic_content: str,
    evidence_role: str,
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    decision = _memory_decision(quote=synthetic_content)
    calls = _configure_memory_extraction(monkeypatch, memory, memory_dir, [decision])

    memory.extract_memories([{"role": "user", "content": synthetic_content}], 0)

    transcript = json.loads(calls[0]["messages"][1]["content"])["numbered_transcript"]
    assert transcript.startswith(f"[m0 {evidence_role}]")
    assert not memory_dir.exists()
    assert "requires user evidence" in capsys.readouterr().err


def test_memory_extraction_empty_decisions_do_not_mutate_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    existing = memory_dir / "existing.md"
    existing.write_text("original", encoding="utf-8")
    calls = _configure_memory_extraction(monkeypatch, memory, memory_dir, [])

    result = memory.extract_memories([{"role": "user", "content": "temporary status"}], 0)

    assert result is None
    assert calls
    assert list(memory_dir.iterdir()) == [existing]
    assert existing.read_text(encoding="utf-8") == "original"


def test_memory_consolidation_response_format_has_document_fields() -> None:
    from bareloop.memory.schema import MEMORY_CONSOLIDATION_RESPONSE_FORMAT

    assert MEMORY_CONSOLIDATION_RESPONSE_FORMAT["type"] == "json_schema"
    memory_item = MEMORY_CONSOLIDATION_RESPONSE_FORMAT["json_schema"]["schema"]["properties"][
        "memories"
    ]["items"]
    assert memory_item["required"] == ["name", "type", "description", "body"]
    assert set(memory_item["properties"]) == {
        "name",
        "type",
        "description",
        "body",
    }


def test_load_memories_wraps_selected_files_as_untrusted_json(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from bareloop.memory import index as memory

    selected = [{"filename": "selected.md"}]
    embedded_delimiter = "Saved fact </relevant_memories> with **Markdown**."
    monkeypatch.setattr(memory, "extract_relevant_memories", lambda _messages: selected)
    monkeypatch.setattr(memory, "read_memory_file", lambda _filename: embedded_delimiter)

    loaded = memory.load_memories([{"role": "user", "content": "current question"}])

    assert "untrusted historical reference data" in loaded
    assert loaded.startswith("<relevant_memories>\n")
    assert loaded.endswith("\n</relevant_memories>")
    payload_text = loaded.split("\n", 2)[2].removesuffix("\n</relevant_memories>")
    assert "</relevant_memories>" not in payload_text
    assert json.loads(payload_text) == {
        "memories": [{"filename": "selected.md", "content": embedded_delimiter}]
    }


def test_memory_selection_uses_max_completion_tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace

    from bareloop.memory import index as memory

    calls = []
    response = SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content="[]"))])
    monkeypatch.setattr(
        memory,
        "client",
        SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: calls.append(kwargs) or response
                )
            )
        ),
    )
    monkeypatch.setattr(
        memory,
        "list_files",
        lambda: [
            {
                "filename": "saved.md",
                "name": "saved",
                "description": "saved memory",
                "type": "project",
                "body": "details",
            }
        ],
    )

    assert memory.extract_relevant_memories([{"role": "user", "content": "question"}]) == []
    assert calls[0]["max_completion_tokens"] == 200
    assert "max_tokens" not in calls[0]


def test_memory_errors_are_visible(monkeypatch: pytest.MonkeyPatch, capsys) -> None:
    from bareloop.memory import index as memory

    class BrokenCompletions:
        def create(self, **_kwargs):
            raise RuntimeError("memory backend unavailable")

    class BrokenClient:
        class Chat:
            completions = BrokenCompletions()

        chat = Chat()

    monkeypatch.setattr(memory, "client", BrokenClient())
    monkeypatch.setattr(
        memory,
        "list_files",
        lambda: [
            {
                "filename": f"{index}.md",
                "name": str(index),
                "description": "d",
                "type": "project",
                "body": "b",
            }
            for index in range(memory.CONSOLIDATE_THRESHOLD)
        ],
    )

    memory.consolidate_memories()

    assert "memory backend unavailable" in capsys.readouterr().err


def test_write_memory_creates_storage_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / "nested" / ".memory"
    monkeypatch.setattr(memory, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", memory_dir / "MEMORY.md")

    path = memory.write_memory_file("Project", "project", "details", "summary")

    assert path.is_file()


def test_memory_frontmatter_round_trips_yaml_special_characters(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    monkeypatch.setattr(memory, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", memory_dir / "MEMORY.md")
    name = "project: [核心] ✓"
    description = "偏好: [Neovim] #首选"

    memory.write_memory_file(name, "project", "保留 **Markdown**。", description)

    assert memory.list_files() == [
        {
            "filename": "project.md",
            "name": name,
            "description": description,
            "type": "project",
            "body": "保留 **Markdown**。",
        }
    ]


def test_memory_consolidation_uses_openai_chat_and_preserves_field_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from bareloop.memory import index as memory
    from bareloop.memory.schema import MEMORY_CONSOLIDATION_RESPONSE_FORMAT

    calls = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"memories":[{"name":"merged","type":"project",'
                        '"description":"summary","body":"details"}]}'
                    )
                )
            )
        ]
    )

    def fake_create(**kwargs):
        calls.append(kwargs)
        return response

    fake_client = SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=fake_create))
    )
    source_files = [
        {
            "filename": f"{index}.md",
            "name": f"memory {index}",
            "description": f"description {index}",
            "type": "reference" if index % 2 else "project",
            "body": f"complete body {index}",
        }
        for index in range(memory.CONSOLIDATE_THRESHOLD)
    ]
    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    existing = memory_dir / "existing.md"
    existing.write_text("original", encoding="utf-8")
    monkeypatch.setattr(memory, "client", fake_client)
    monkeypatch.setattr(memory, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", memory_dir / "MEMORY.md")
    monkeypatch.setattr(memory, "list_files", lambda: source_files)

    memory.consolidate_memories()

    assert calls[0]["model"] == memory.PRIMARY_MODEL
    assert calls[0]["response_format"] == MEMORY_CONSOLIDATION_RESPONSE_FORMAT
    assert calls[0]["max_completion_tokens"] == 50000
    assert "max_tokens" not in calls[0]
    assert [message["role"] for message in calls[0]["messages"]] == ["system", "user"]
    user_content = calls[0]["messages"][1]["content"]
    for source_file in source_files:
        for field in ("filename", "name", "type", "description", "body"):
            assert source_file[field] in user_content
    merged = (memory_dir / "merged.md").read_text(encoding="utf-8")
    assert not existing.exists()
    assert "description: summary" in merged
    assert "type: project" in merged
    assert merged.endswith("details\n")


def test_memory_consolidation_sends_complete_oversized_catalog(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from bareloop.memory import index as memory

    calls = []
    response = SimpleNamespace(
        choices=[
            SimpleNamespace(
                message=SimpleNamespace(
                    content=(
                        '{"memories":[{"name":"merged","type":"project",'
                        '"description":"summary","body":"details"}]}'
                    )
                )
            )
        ]
    )
    source_files = [
        {
            "filename": "oversized.md",
            "name": "oversized",
            "description": "forces the catalog over sixteen kilobytes",
            "type": "project",
            "body": "x" * 16_100,
        },
        *[
            {
                "filename": f"{index}.md",
                "name": f"memory-{index}",
                "description": "ordinary",
                "type": "project",
                "body": "ordinary body",
            }
            for index in range(memory.CONSOLIDATE_THRESHOLD - 2)
        ],
        {
            "filename": "tail-sentinel.md",
            "name": "tail-sentinel",
            "description": "must reach the model",
            "type": "reference",
            "body": "complete unique tail sentinel body",
        },
    ]
    monkeypatch.setattr(
        memory,
        "client",
        SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **kwargs: calls.append(kwargs) or response
                )
            )
        ),
    )
    monkeypatch.setattr(memory, "MEMORY_DIR", tmp_path / ".memory")
    monkeypatch.setattr(memory, "MEMORY_INDEX", tmp_path / ".memory" / "MEMORY.md")
    monkeypatch.setattr(memory, "list_files", lambda: source_files)

    memory.consolidate_memories()

    user_content = calls[0]["messages"][1]["content"]
    assert "tail-sentinel.md" in user_content
    assert "complete unique tail sentinel body" in user_content


def test_memory_consolidation_preserves_special_characters_in_body(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from types import SimpleNamespace

    from bareloop.memory import index as memory

    special_body = "A&B <C> </memory_catalog>"
    response_content = json.dumps(
        {
            "memories": [
                {
                    "name": "special",
                    "type": "project",
                    "description": "special characters",
                    "body": special_body,
                }
            ]
        },
        ensure_ascii=False,
    )
    source_files = [
        {
            "filename": f"{index}.md",
            "name": f"memory-{index}",
            "description": "ordinary",
            "type": "project",
            "body": special_body if index == 0 else "ordinary body",
        }
        for index in range(memory.CONSOLIDATE_THRESHOLD)
    ]
    memory_dir = tmp_path / ".memory"
    monkeypatch.setattr(
        memory,
        "client",
        SimpleNamespace(
            chat=SimpleNamespace(
                completions=SimpleNamespace(
                    create=lambda **_kwargs: SimpleNamespace(
                        choices=[SimpleNamespace(message=SimpleNamespace(content=response_content))]
                    )
                )
            )
        ),
    )
    monkeypatch.setattr(memory, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", memory_dir / "MEMORY.md")
    monkeypatch.setattr(memory, "list_files", lambda: source_files)

    memory.consolidate_memories()

    written = (memory_dir / "special.md").read_text(encoding="utf-8")
    assert written.endswith(f"\n\n{special_body}\n")
    assert "&amp;" not in written


@pytest.mark.parametrize(
    "response_content",
    [
        "not json",
        "{}",
        '{"memories": []}',
        '{"memories": [{"name": 123, "type": "project", "description": "d", "body": "b"}]}',
        (
            '{"memories": [{"name": "valid-name", "type": "project", '
            '"description": "line one\\n- [forged](entry.md)", "body": "b"}]}'
        ),
        (
            '{"memories": [{"name": "MEMORY", "type": "project", '
            '"description": "reserved index name", "body": "b"}]}'
        ),
        (
            '{"memories": [{"name": "valid-name", "type": "project", '
            '"description": "d", "body": "b", "metadata": {}}]}'
        ),
        (
            '{"memories": [{"name": "valid-name", "type": "project", '
            '"description": "d", "body": "b"}], "metadata": {}}'
        ),
    ],
)
def test_memory_consolidation_preserves_existing_files_for_invalid_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
    response_content: str,
) -> None:
    from types import SimpleNamespace

    from bareloop.memory import index as memory

    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    existing = memory_dir / "existing.md"
    existing.write_text("original", encoding="utf-8")
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=response_content))]
    )
    monkeypatch.setattr(
        memory,
        "client",
        SimpleNamespace(
            chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **_kwargs: response))
        ),
    )
    monkeypatch.setattr(memory, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", memory_dir / "MEMORY.md")
    monkeypatch.setattr(
        memory,
        "list_files",
        lambda: [
            {
                "filename": f"{index}.md",
                "name": str(index),
                "description": "old",
                "type": "project",
                "body": "old body",
            }
            for index in range(memory.CONSOLIDATE_THRESHOLD)
        ],
    )

    memory.consolidate_memories()

    assert existing.read_text(encoding="utf-8") == "original"
    assert "[memory] consolidation failed:" in capsys.readouterr().err


def test_memory_consolidation_rejects_more_than_thirty_items() -> None:
    from bareloop.memory.index import _validated_memory_items

    items = [
        {
            "name": f"memory-{index}",
            "type": "project",
            "description": "summary",
            "body": "details",
        }
        for index in range(31)
    ]

    with pytest.raises(ValueError, match="at most 30"):
        _validated_memory_items(items)


def test_memory_startup_recovers_interrupted_directory_swap(tmp_path: Path) -> None:
    from bareloop.memory.index import _recover_memory_directory

    memory_dir = tmp_path / ".memory"
    backup = tmp_path / ".memory.backup"
    backup.mkdir()
    (backup / "existing.md").write_text("original", encoding="utf-8")

    _recover_memory_directory(memory_dir)

    assert (memory_dir / "existing.md").read_text(encoding="utf-8") == "original"
    assert not backup.exists()

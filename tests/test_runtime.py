import asyncio
from pathlib import Path

import pytest


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

    async def prompt_async(_prompt: str) -> str:
        await never_finishes.wait()
        return "unreachable"

    monkeypatch.setattr(mian.PROMPT_SESSION, "prompt_async", prompt_async)
    monkeypatch.setattr(mian.BUS, "peek", lambda _recipient: ["event"])

    result = asyncio.run(asyncio.wait_for(mian.wait_for_cli_event(), timeout=0.2))

    assert result == ("wake", None)


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


def test_memory_consolidation_uses_openai_chat_and_preserves_field_order(
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
                        '[{"name":"merged","type":"project",'
                        '"description":"summary","body":"details"}]'
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
            "name": str(index),
            "description": "old",
            "type": "project",
            "body": "old body",
        }
        for index in range(memory.CONSOLIDATE_THRESHOLD)
    ]
    memory_dir = tmp_path / ".memory"
    memory_dir.mkdir()
    monkeypatch.setattr(memory, "client", fake_client)
    monkeypatch.setattr(memory, "MEMORY_DIR", memory_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", memory_dir / "MEMORY.md")
    monkeypatch.setattr(memory, "list_files", lambda: source_files)

    memory.consolidate_memories()

    assert calls[0]["model"] == memory.PRIMARY_MODEL
    merged = (memory_dir / "merged.md").read_text(encoding="utf-8")
    assert "description: summary" in merged
    assert "type: project" in merged
    assert merged.endswith("details\n")


@pytest.mark.parametrize(
    "response_content",
    ["[]", '[{"name": 123, "type": "project", "description": "d", "body": "b"}]'],
)
def test_memory_consolidation_preserves_existing_files_for_invalid_output(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
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

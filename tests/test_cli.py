"""Tests for CLI functionality."""

import sys
from io import StringIO
from unittest.mock import Mock, call

import pytest


def test_model_loading_initialization():
    """Test ModelLoading class initialization."""
    from bareloop.cli_loading import ModelLoading

    # Test with default parameters
    loader = ModelLoading()
    assert loader._text == "thinking…"
    assert loader._stream == sys.stderr
    assert loader._interval == 0.08
    assert loader._enabled is sys.stderr.isatty()

    # Test with custom parameters
    custom_stream = StringIO()
    loader = ModelLoading(text="Custom text", stream=custom_stream, interval=0.1)
    assert loader._text == "Custom text"
    assert loader._stream == custom_stream
    assert loader._interval == 0.1


def test_model_loading_disabled_when_not_tty():
    """Test that ModelLoading is disabled when output is not a TTY."""
    from bareloop.cli_loading import ModelLoading

    # Mock a non-TTY stream
    mock_stream = Mock()
    mock_stream.isatty.return_value = False

    loader = ModelLoading(stream=mock_stream)
    assert not loader._enabled


def test_model_loading_context_manager():
    """Test ModelLoading context manager behavior."""
    from io import StringIO

    from bareloop.cli_loading import ModelLoading

    output = StringIO()

    with ModelLoading(stream=output, enabled=True) as loader:
        # Inside the context, thread should be running (or started)
        assert loader._enabled is True

    # After exiting, thread should be stopped and output cleared
    result = output.getvalue()
    # Should have cleared the line
    assert "\r\033[2K" in result


def test_model_loading_animation():
    """Test that ModelLoading produces animation frames."""
    import time
    from io import StringIO

    from bareloop.cli_loading import ModelLoading

    output = StringIO()

    with ModelLoading(stream=output, interval=0.01, enabled=True):
        # Give it a moment to render a few frames
        time.sleep(0.05)

    result = output.getvalue()
    # Should contain animation frames
    assert "thinking…" in result


def test_cli_loading_frames():
    """Test that ModelLoading has the expected animation frames."""
    from bareloop.cli_loading import ModelLoading

    loader = ModelLoading()
    expected_frames = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")
    assert expected_frames == loader.FRAMES


def test_import_cli_module():
    """Test that the CLI module can be imported successfully."""
    try:
        import bareloop.cli_loading

        assert bareloop.cli_loading is not None
    except ImportError as e:
        pytest.fail(f"Failed to import cli_loading module: {e}")


def test_import_main_module():
    """Test that the main module can be imported successfully."""
    # This will test if all dependencies are properly set up
    try:
        from bareloop import main

        assert main is not None
    except ImportError as e:
        pytest.fail(f"Failed to import main module: {e}")


def test_wait_for_cli_event_structure():
    """Test the structure of wait_for_cli_event function."""
    from bareloop.main import wait_for_cli_event

    # Test that the function exists and is callable
    assert callable(wait_for_cli_event)

    # Check function signature
    import inspect

    sig = inspect.signature(wait_for_cli_event)
    assert "selected_mode" in sig.parameters


def test_build_system_function():
    """Test the build_system function."""
    from bareloop.main import build_system

    system_prompt = build_system()

    # System prompt should contain expected elements
    assert "coding agent" in system_prompt.lower() or "coding" in system_prompt.lower()
    assert "Skill" in system_prompt  # Should mention Skills


def test_format_cli_help_includes_commands_and_context(monkeypatch):
    """Test that /help output lists commands and runtime context."""
    import bareloop.main as main

    monkeypatch.setattr(main, "WORKDIR", "/tmp/workspace")
    monkeypatch.setattr(
        main,
        "SKILL_REGISTRY",
        {
            "planner": {"name": "planner", "description": "Plan work"},
            "tester": {"name": "tester", "description": "Test work"},
        },
    )

    help_text = main.format_cli_help()

    assert "/mode" in help_text
    assert "/clear" in help_text
    assert "/help" in help_text
    assert "q, quit, exit" in help_text
    assert "Esc+Enter" in help_text
    assert "Ctrl+J" in help_text
    assert "Workspace: /tmp/workspace" in help_text
    assert "Loaded skills: 2 skills" in help_text


def test_wait_for_cli_event_handles_help(monkeypatch, capsys):
    """Test that /help is handled locally without starting an agent turn."""
    import asyncio
    from types import SimpleNamespace

    import bareloop.main as main
    from bareloop.mode import AgentMode

    async def prompt_async(*args, **kwargs):
        return "/help"

    monkeypatch.setattr(main, "PROMPT_SESSION", SimpleNamespace(prompt_async=prompt_async))
    monkeypatch.setattr(main, "format_cli_help", lambda: "help text")

    kind, content, mode = asyncio.run(main._wait_for_cli_event(AgentMode.NORMAL))

    assert (kind, content, mode) == ("next", None, AgentMode.NORMAL)
    assert "help text" in capsys.readouterr().out


def test_run_agent_turn_locked_structure():
    """Test the structure of run_agent_turn_locked function."""
    from bareloop.main import run_agent_turn_locked

    # Test that the function exists and is callable
    assert callable(run_agent_turn_locked)

    # Check function signature
    import inspect

    sig = inspect.signature(run_agent_turn_locked)
    params = list(sig.parameters.keys())
    assert "messages" in params
    assert "tw" in params  # TraceWriter
    assert "user_input" in params


def test_create_session_structure():
    """Test the structure of create_session function."""
    from bareloop.main import create_session

    # Test that the function exists and is callable
    assert callable(create_session)


def test_format_tool_args_summary():
    """Test format_tool_args_summary helper for various tools."""
    from bareloop.cli_loading import format_tool_args_summary

    assert format_tool_args_summary("bash", {"command": "git status"}) == "git status"
    assert format_tool_args_summary("read", {"path": "README.md"}) == "path='README.md'"
    assert format_tool_args_summary("glob", {"pattern": "**/*.py"}) == "pattern='**/*.py'"
    assert format_tool_args_summary("custom", {"foo": "bar"}) == "foo=bar"
    assert "…" in format_tool_args_summary("bash", {"command": "x" * 60})
    assert format_tool_args_summary("bash", "raw string argument") == "raw string argument"


def test_agent_turn_ui_lifecycle_and_collapse():
    """Test that AgentTurnUI displays tool executions and collapses on completion."""
    from io import StringIO

    from bareloop.cli_loading import AgentTurnUI

    stream = StringIO()
    ui = AgentTurnUI(stream=stream, enabled=True, collapse_on_finish=True)
    assert ui.enabled is True

    # 1. Start thinking
    ui.start_thinking(round_num=1)
    # 2. Start tool
    ui.start_tool("bash", {"command": "git status"})
    # 3. Finish tool
    ui.finish_tool("bash", duration_ms=25.0, outcome="success")
    # 4. Finish turn (should collapse)
    ui.finish_turn()

    output = stream.getvalue()
    # Verifies tool finished indicator was emitted
    assert "[工具] bash" in output
    # Verifies collapsed summary badge was rendered
    assert "已完成思考与工具调用" in output
    # Verifies cursor-up ANSI codes were used for erasing intermediate lines
    assert "\033[1A\033[2K" in output


def test_loop_execution_result_bool():
    """Test that LoopExecutionResult evaluates as a boolean based on completed."""
    from bareloop.loop import LoopExecutionResult
    from bareloop.telemetry import RunTelemetry, RunTermination

    ok_res = LoopExecutionResult(
        completed=True,
        final_output="done",
        tool_calls=1,
        rounds=1,
        model="test",
        telemetry=RunTelemetry(),
        termination=RunTermination.COMPLETED,
    )
    assert bool(ok_res) is True

    fail_res = LoopExecutionResult(
        completed=False,
        final_output="",
        tool_calls=0,
        rounds=1,
        model="test",
        telemetry=RunTelemetry(),
        termination=RunTermination.PROVIDER_ERROR,
    )
    assert bool(fail_res) is False


@pytest.mark.parametrize("mode_name", ["NORMAL", "GOAL"])
def test_clear_command_is_a_local_event(monkeypatch, mode_name):
    import asyncio

    from bareloop import main

    mode = getattr(main.AgentMode, mode_name)

    async def prompt_async(*_args, **_kwargs):
        return "/clear"

    monkeypatch.setattr(main.PROMPT_SESSION, "prompt_async", prompt_async)
    monkeypatch.setattr(main.BUS, "peek", lambda _recipient: [])

    assert asyncio.run(main.wait_for_cli_event(mode)) == ("clear", None, mode)


@pytest.mark.parametrize("with_history", [False, True])
def test_clear_preserves_shared_session_and_skips_agent_turn(monkeypatch, capsys, with_history):
    from bareloop import main

    shared_messages = []
    snapshots = []
    events = iter(["clear", "clear", "user", "quit"])
    trace = Mock()
    agent_turn = Mock()

    def capture_session(messages, _trace):
        shared_messages.append(messages)
        if with_history:
            messages.extend(
                [
                    {"role": "user", "content": "old topic"},
                    {"role": "assistant", "content": "old answer"},
                    {"role": "tool", "content": "old result", "tool_call_id": "old"},
                ]
            )

    async def wait_for_event(mode):
        kind = next(events)
        snapshots.append(list(shared_messages[0]))
        return kind, "new topic" if kind == "user" else None, mode

    monkeypatch.setattr(main, "init_hooks", lambda: None)
    monkeypatch.setattr(main, "start_background_mcp_init", lambda: None)
    monkeypatch.setattr(main, "_scan_skills", lambda: None)
    monkeypatch.setattr(main, "build_system", lambda: "initial system prompt")
    monkeypatch.setattr(main, "TraceWriter", lambda: trace)
    monkeypatch.setattr(main, "start_cron_scheduler", capture_session)
    monkeypatch.setattr(main, "wait_for_cli_event", wait_for_event)
    monkeypatch.setattr(main, "run_agent_turn_locked", agent_turn)
    monkeypatch.setattr(main, "active_teammates", {})

    main.create_session()

    expected = [{"role": "system", "content": "initial system prompt"}]
    assert snapshots[1:] == [expected, expected, expected]
    assert shared_messages[0] == expected
    assert agent_turn.call_count == 1
    args = agent_turn.call_args.args
    assert args[0] is shared_messages[0]
    assert args[1:4] == (trace, "new topic", main.DEFAULT_MODE)
    assert capsys.readouterr().out.count("Conversation cleared.") == 2
    assert trace.write.call_args_list == [
        call(event_type="用户输入", data="new topic"),
        call(event_type="停止对话"),
    ]

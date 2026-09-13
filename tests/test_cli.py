"""Tests for CLI functionality."""

import pytest
from unittest.mock import Mock, patch, MagicMock
from io import StringIO
import sys


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
    assert loader._enabled == False


def test_model_loading_context_manager():
    """Test ModelLoading context manager behavior."""
    from bareloop.cli_loading import ModelLoading
    from io import StringIO
    
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
    from bareloop.cli_loading import ModelLoading
    from io import StringIO
    import time
    
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
    assert loader.FRAMES == expected_frames


def test_import_cli_module():
    """Test that the CLI module can be imported successfully."""
    try:
        import bareloop.cli_loading
        assert True
    except ImportError as e:
        pytest.fail(f"Failed to import cli_loading module: {e}")


def test_import_main_module():
    """Test that the main module can be imported successfully."""
    # This will test if all dependencies are properly set up
    try:
        from bareloop import mian
        assert True
    except ImportError as e:
        pytest.fail(f"Failed to import mian module: {e}")


def test_wait_for_cli_event_structure():
    """Test the structure of wait_for_cli_event function."""
    from bareloop.mian import wait_for_cli_event
    from bareloop.mode import AgentMode
    
    # Test that the function exists and is callable
    assert callable(wait_for_cli_event)
    
    # Check function signature
    import inspect
    sig = inspect.signature(wait_for_cli_event)
    assert 'selected_mode' in sig.parameters


def test_build_system_function():
    """Test the build_system function."""
    from bareloop.mian import build_system
    
    system_prompt = build_system()
    
    # System prompt should contain expected elements
    assert "coding agent" in system_prompt.lower() or "coding" in system_prompt.lower()
    assert "Skill" in system_prompt  # Should mention Skills


def test_run_agent_turn_locked_structure():
    """Test the structure of run_agent_turn_locked function."""
    from bareloop.mian import run_agent_turn_locked
    
    # Test that the function exists and is callable
    assert callable(run_agent_turn_locked)
    
    # Check function signature
    import inspect
    sig = inspect.signature(run_agent_turn_locked)
    params = list(sig.parameters.keys())
    assert 'messages' in params
    assert 'tw' in params  # TraceWriter
    assert 'user_input' in params


def test_create_session_structure():
    """Test the structure of create_session function."""
    from bareloop.mian import create_session
    
    # Test that the function exists and is callable
    assert callable(create_session)

import os
import signal
import subprocess
import threading
from contextlib import suppress
from pathlib import Path

from bareloop.settings import WORKDIR
from bareloop.worktree import get_agent_cwd

_RUNNING_PROCESSES: set[subprocess.Popen[str]] = set()
_PROCESS_LOCK = threading.RLock()


def format_shell_result(output: str, exit_code: int | None) -> str:
    if exit_code in (0, None):
        return output
    return f"Error: command exited with code {exit_code}\n{output}"


def run_shell_process(
    command: str,
    cwd: str | Path | None = None,
    timeout: int = 120,
) -> tuple[str, int | None]:
    process: subprocess.Popen[str] | None = None
    try:
        process = subprocess.Popen(
            command,
            shell=True,
            cwd=Path(cwd).resolve() if cwd is not None else WORKDIR,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        with _PROCESS_LOCK:
            _RUNNING_PROCESSES.add(process)
        stdout, stderr = process.communicate(timeout=timeout)
        output = (stdout + stderr).strip()
        return (output[:50000] if output else "(no output)"), process.returncode
    except subprocess.TimeoutExpired:
        return f"Error: timeout after {timeout}s", None
    except OSError as error:
        return f"Error: {type(error).__name__}: {error}", None
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGTERM)
                    process.wait(timeout=0.5)
                except (ProcessLookupError, OSError, subprocess.TimeoutExpired):
                    with suppress(ProcessLookupError, OSError):
                        os.killpg(process.pid, signal.SIGKILL)
            with _PROCESS_LOCK:
                _RUNNING_PROCESSES.discard(process)


def run_bash(
    command: str,
    cwd: str | Path | None = None,
) -> str:
    return format_shell_result(*run_shell_process(command, cwd=cwd))


def run_agent_bash(command: str) -> str:
    cwd, error = get_agent_cwd()
    if error:
        return error
    return run_bash(command, cwd=cwd)

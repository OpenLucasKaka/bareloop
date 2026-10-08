import itertools
import sys
import threading
from types import TracebackType
from typing import Any, TextIO


class ModelLoading:
    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(
        self,
        text: str = "thinking…",
        *,
        stream: TextIO | None = None,
        interval: float = 0.08,
        enabled: bool | None = None,
    ):
        self._text = text
        self._stream = stream or sys.stderr
        self._interval = interval
        # 只有真实终端才显示，pytest、文件重定向时自动关闭
        self._enabled = self._stream.isatty() if enabled is None else enabled
        self._frames = itertools.cycle(self.FRAMES)
        # 用于通知后台线程停止
        self._stopped = threading.Event()
        # 保存后台线程对象
        self._thread: threading.Thread | None = None

    def __enter__(self):
        if not self._enabled:
            return self

        # 立即渲染第一帧，不必等待线程调度。
        self._render()
        self._thread = threading.Thread(
            target=self._animate,
            name="model-loading",
            daemon=True,
        )
        self._thread.start()
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc_value: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        if not self._enabled:
            return

        self._stopped.set()
        if self._thread is not None:
            self._thread.join()

        # 清除整行，避免 loading 残留。
        self._stream.write("\r\033[2K")
        self._stream.flush()

    def _animate(self) -> None:
        while not self._stopped.wait(self._interval):
            self._render()

    def _render(self) -> None:
        frame = next(self._frames)
        self._stream.write(f"\r\033[90m{frame} {self._text}\033[0m")
        self._stream.flush()


def format_tool_args_summary(name: str, args: Any) -> str:
    """Formats a concise one-line summary of tool arguments for CLI status display."""
    if not isinstance(args, dict):
        arg_str = str(args).strip().replace("\n", " ")
        return (arg_str[:45] + "…") if len(arg_str) > 45 else arg_str

    if name == "bash" and "command" in args:
        cmd = str(args["command"]).strip().replace("\n", " ")
        return (cmd[:40] + "…") if len(cmd) > 40 else cmd
    if name in {"read", "write", "edit"} and "path" in args:
        return f"path='{args['path']}'"
    if name == "glob" and "pattern" in args:
        return f"pattern='{args['pattern']}'"

    for key, val in args.items():
        v_str = str(val).strip().replace("\n", " ")
        if len(v_str) > 30:
            v_str = v_str[:27] + "…"
        return f"{key}={v_str}"
    return ""


class AgentTurnUI:
    """Manages real-time display of LLM thinking and tool calls during a turn,
    and folds/collapses the intermediate process into a clean badge upon completion."""

    def __init__(
        self,
        *,
        stream: TextIO | None = None,
        enabled: bool | None = None,
        collapse_on_finish: bool = True,
    ):
        from time import perf_counter

        self._stream = stream or sys.stderr
        self._enabled = self._stream.isatty() if enabled is None else enabled
        self._collapse_on_finish = collapse_on_finish
        self._lines_printed = 0
        self._tool_calls = 0
        self._rounds = 0
        self._started_at = perf_counter()
        self._active_loading: ModelLoading | None = None

    @property
    def enabled(self) -> bool:
        return self._enabled

    def start_thinking(self, round_num: int) -> None:
        if not self._enabled:
            return
        self.stop_loading()
        self._rounds = round_num
        self._active_loading = ModelLoading(
            text=f"思考中… (第 {round_num} 轮)",
            stream=self._stream,
            enabled=True,
        )
        self._active_loading.__enter__()

    def stop_loading(self) -> None:
        if self._active_loading is not None:
            self._active_loading.__exit__(None, None, None)
            self._active_loading = None

    def start_tool(self, name: str, args: Any) -> None:
        if not self._enabled:
            return
        self.stop_loading()
        self._tool_calls += 1
        summary = format_tool_args_summary(name, args)
        label = f"调用工具: {name}({summary})" if summary else f"调用工具: {name}"
        self._active_loading = ModelLoading(
            text=f"\033[33m⚙️ {label}…\033[0m",
            stream=self._stream,
            enabled=True,
        )
        self._active_loading.__enter__()

    def finish_tool(self, name: str, duration_ms: float, outcome: str = "success") -> None:
        if not self._enabled:
            return
        self.stop_loading()
        icon = "✔" if outcome == "success" else "✖"
        color = "90m" if outcome == "success" else "31m"
        line = f"\r\033[2K\033[{color}{icon} [工具] {name} ({duration_ms:.0f}ms)\033[0m\n"
        self._stream.write(line)
        self._stream.flush()
        self._lines_printed += 1

    def finish_turn(self) -> None:
        if not self._enabled:
            return
        from time import perf_counter

        self.stop_loading()
        if self._collapse_on_finish and self._lines_printed > 0:
            # Erase intermediate lines from terminal
            for _ in range(self._lines_printed):
                self._stream.write("\033[1A\033[2K")
            elapsed = perf_counter() - self._started_at
            badge = (
                f"\r\033[2K\033[90m✔ 已完成思考与工具调用 "
                f"(共 {self._rounds} 轮 · {self._tool_calls} 次工具 · "
                f"耗时 {elapsed:.1f}s)\033[0m\n\n"
            )
            self._stream.write(badge)
            self._stream.flush()
        self._lines_printed = 0


import itertools
import sys
import threading
from types import TracebackType
from typing import TextIO


class ModelLoading:
    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(
        self,
        text: str = "正在思考…",
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

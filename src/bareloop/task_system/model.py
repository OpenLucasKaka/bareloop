from __future__ import annotations

import fcntl
import json
import os
import re
import secrets
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from pathlib import Path

from bareloop.settings import WORKDIR

TASK_ID_PATTERN = re.compile(r"task_[0-9a-f]{8}")
TASK_STATUSES = frozenset({"pending", "in_process", "completed"})
TASK_DIR = WORKDIR / ".bareloop" / ".tasks"
TASK_LOCK_PATH = TASK_DIR / ".lock"

_task_thread_lock = threading.RLock()
_task_store_state = threading.local()


@contextmanager
def task_lock() -> Iterator[None]:
    """Serialize task operations across threads and processes."""
    with _task_thread_lock:
        depth = getattr(_task_store_state, "depth", 0)
        if depth == 0:
            TASK_LOCK_PATH.parent.mkdir(parents=True, exist_ok=True)
            handle = TASK_LOCK_PATH.open("a+")
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            except BaseException:
                handle.close()
                raise
            _task_store_state.handle = handle
        _task_store_state.depth = depth + 1
        try:
            yield
        finally:
            remaining = _task_store_state.depth - 1
            if remaining:
                _task_store_state.depth = remaining
            else:
                handle = _task_store_state.handle
                try:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                finally:
                    handle.close()
                    del _task_store_state.handle
                    del _task_store_state.depth


@dataclass
class Task:
    id: str
    subject: str
    description: str
    owner: str | None
    blocked_by: list[str]
    status: str
    worktree: str | None = None


class TaskStore:
    def __init__(self, directory: Path):
        self.directory = Path(directory)

    def _root(self, *, create: bool = False) -> Path:
        root = self.directory.resolve()
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    def get_path(self, task_id: str, *, create_root: bool = False) -> Path:
        if not isinstance(task_id, str) or TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise ValueError(f"Invalid task id: {task_id}")
        root = self._root(create=create_root)
        path = (root / f"{task_id}.json").resolve()
        if not path.is_relative_to(root):
            raise ValueError(f"Invalid task id: {task_id}")
        return path

    def exists(self, task_id: str) -> bool:
        return self.get_path(task_id).is_file()

    def _validate(self, task: Task) -> None:
        self.get_path(task.id)
        if not isinstance(task.subject, str) or not task.subject.strip():
            raise ValueError("subject must be a non-empty string")
        if not isinstance(task.description, str):
            raise ValueError("description must be a string")
        if task.status not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {task.status}")
        if not isinstance(task.blocked_by, list) or any(
            not isinstance(item, str) or TASK_ID_PATTERN.fullmatch(item) is None
            for item in task.blocked_by
        ):
            raise ValueError("blocked_by must contain valid task ids")
        if task.worktree is not None and not isinstance(task.worktree, str):
            raise ValueError("worktree must be a string or None")

    def create(
        self,
        subject: str,
        description: str = "",
        blocked_by: list[str] | None = None,
    ) -> Task:
        if not isinstance(subject, str) or not subject.strip():
            raise ValueError("subject must be a non-empty string")
        if not isinstance(description, str):
            raise ValueError("description must be a string")
        if blocked_by is not None and not isinstance(blocked_by, list):
            raise ValueError("blocked_by must be a list of task ids")

        dependencies = list(dict.fromkeys(blocked_by or []))
        for dependency in dependencies:
            if not isinstance(dependency, str) or TASK_ID_PATTERN.fullmatch(dependency) is None:
                raise ValueError(f"Invalid task id: {dependency}")

        with task_lock():
            missing = [task_id for task_id in dependencies if not self.exists(task_id)]
            if missing:
                raise ValueError(f"dependency does not exist: {', '.join(missing)}")
            for _ in range(100):
                task = Task(
                    id=f"task_{secrets.token_hex(4)}",
                    subject=subject,
                    description=description,
                    blocked_by=dependencies,
                    status="pending",
                    owner=None,
                )
                try:
                    with self.get_path(task.id, create_root=True).open(
                        "x", encoding="utf-8"
                    ) as file:
                        json.dump(asdict(task), file, indent=2)
                    return task
                except FileExistsError:
                    continue
        raise RuntimeError("Could not allocate a unique task ID")

    def save(self, task: Task) -> None:
        if not isinstance(task, Task):
            raise TypeError("task must be a Task")
        self._validate(task)
        with task_lock():
            path = self.get_path(task.id, create_root=True)
            temporary_path = path.with_name(
                f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
            )
            try:
                temporary_path.write_text(json.dumps(asdict(task), indent=2), encoding="utf-8")
                os.replace(temporary_path, path)
            finally:
                temporary_path.unlink(missing_ok=True)

    def load(self, task_id: str) -> Task:
        with task_lock():
            path = self.get_path(task_id)
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError(f"Invalid task data: {task_id}")
            data.setdefault("worktree", None)
            try:
                task = Task(**data)
            except TypeError as exc:
                raise ValueError(f"Invalid task data: {task_id}: {exc}") from exc
            if task.id != task_id:
                raise ValueError(f"Task id does not match filename: {task_id}")
            self._validate(task)
            return task

    def list(self) -> list[Task]:
        root = self._root()
        if not root.exists():
            return []
        with task_lock():
            return [self.load(path.stem) for path in sorted(root.glob("task_*.json"))]

import json, fcntl, re, secrets, threading
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from bareloop.settings import WORKDIR
from contextlib import contextmanager


TASK_ID_PATTERN = re.compile(r"task_[0-9a-f]{8}")
TASK_STATUSES = frozenset({"pending", "in_process", "completed"})
TASK_DIR = WORKDIR / ".bareloop" / ".tasks"
task_lock = threading.RLock()
_task_store_state = threading.local()
TASK_LOCK_PATH = TASK_DIR / '.lock'

@contextmanager
def task_lock():
    with task_lock:
        depth = getattr(_task_store_state, 'depth', 0)
        if depth == 0:
            TASK_DIR.mkdir(parents=True, exist_ok=True)
            handle = TASK_LOCK_PATH.open("a+")
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
            _task_store_state.handle = handle
            _task_store_state.depth = depth + 1
        try:
            yield
        finally:
            _task_store_state.depth -= 1
            if _task_store_state.depth == 0:
                handle = _task_store_state.handle
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                handle.close()
                del _task_store_state.handle




@dataclass
class Task:
    id: str
    subject: str
    description: str
    owner: str | None
    blocked_by: list[str]
    status: str
    worktree: str | None


class TaskStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def _root(self, *, create: bool = False) -> Path:
        root = self.directory.resolve()
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    def get_path(self, task_id: str, *, create_root: bool = False) -> Path:
        if not isinstance(task_id, str) or TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise ValueError(f"Invalid task id: {task_id}")
        path = self._root(create=create_root) / f"{task_id}.json"
        if (not TASK_DIR.is_relative_to(WORKDIR.resolve())
                or not path.is_relative_to(TASK_DIR)):
            raise ValueError(f"Invalid task ID: {task_id!r}")
        return path

    def exists(self, task_id: str) -> bool:
        return self.get_path(task_id).is_file()

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

        dependencies = list(dict.fromkeys(blocked_by or []))
        missing = [task_id for task_id in dependencies if not self.exists(task_id)]
        if missing:
            raise ValueError(f"dependency does not exist: {', '.join(missing)}")
        with task_lock:
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
                    with self.get_path(task.id, create_root=True).open("x", encoding="utf-8") as file:
                        json.dump(asdict(task), file, indent=2)
                    return task
                except FileExistsError:
                    continue
        raise RuntimeError("Could not allocate a unique task ID")

    def save(self, task: Task) -> None:
        if task.status not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {task.status}")
        with task_lock:
            path = self.get_path(task.id, create_root=True)
            temporary_path = path.with_name(f"{path.name}.{os.getpid()}.{threading.get_ident()}.tmp")
            try:
                temporary_path.write_text(json.dumps(asdict(task), indent=2), encoding="utf-8")
                os.replace(temporary_path, path)
            finally:
                temporary_path.unlink(missing_ok=True)

    def load(self, task_id: str) -> Task:
        with task_lock:
            data = json.loads(self.get_path(task_id).read_text(encoding="utf-8"))
        task = Task(**data)
        if task.id != task_id:
            raise ValueError(f"Task id does not match filename: {task_id}")
        if task.status not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {task.status}")
        return task

    def list(self) -> list[Task]:
        root = self._root()
        if not root.exists():
            return []
        with task_lock:
            return [self.load(path.stem) for path in sorted(root.glob("task_*.json"))]

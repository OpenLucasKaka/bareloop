import json
import re
import secrets
from dataclasses import asdict, dataclass
from pathlib import Path

TASK_ID_PATTERN = re.compile(r"task_[0-9a-f]{8}")
TASK_STATUSES = frozenset({"pending", "in_process", "completed"})


@dataclass
class Task:
    id: str
    subject: str
    description: str
    owner: str | None
    blocked_by: list[str]
    status: str


class TaskStore:
    def __init__(self, directory: Path):
        self.directory = directory

    def _root(self, *, create: bool = False) -> Path:
        root = self.directory.resolve()
        if create:
            root.mkdir(parents=True, exist_ok=True)
        return root

    def _path(self, task_id: str, *, create_root: bool = False) -> Path:
        if not isinstance(task_id, str) or TASK_ID_PATTERN.fullmatch(task_id) is None:
            raise ValueError(f"Invalid task id: {task_id}")
        return self._root(create=create_root) / f"{task_id}.json"

    def exists(self, task_id: str) -> bool:
        return self._path(task_id).is_file()

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
                with self._path(task.id, create_root=True).open("x", encoding="utf-8") as file:
                    json.dump(asdict(task), file, indent=2)
                return task
            except FileExistsError:
                continue
        raise RuntimeError("Could not allocate a unique task ID")

    def save(self, task: Task) -> None:
        if task.status not in TASK_STATUSES:
            raise ValueError(f"Invalid task status: {task.status}")
        self._path(task.id, create_root=True).write_text(
            json.dumps(asdict(task), indent=2),
            encoding="utf-8",
        )

    def load(self, task_id: str) -> Task:
        data = json.loads(self._path(task_id).read_text(encoding="utf-8"))
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
        return [self.load(path.stem) for path in sorted(root.glob("task_*.json"))]

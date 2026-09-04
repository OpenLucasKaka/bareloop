import json, os, threading
from dataclasses import asdict
from bareloop.task_system.model import Task, TaskStore, task_lock, TASK_DIR
from .model import TASK_DIR

TASK = TaskStore(TASK_DIR)


def create_task(
    subject: str,
    description: str = "",
    blocked_by: list[str] | None = None,
) -> Task:
    return TASK.create(subject, description, blocked_by)


def list_tasks() -> list[Task]:
    return TASK.list()



def load_task(task_id: str) -> Task:
    return TASK.load(task_id)

def get_task(task_id: str) -> str:
    return json.dumps(asdict(TASK.load(task_id)), indent=2)


def claim_task(task_id: str, owner: str = "agent") -> Task:
    from bareloop.worktree.index import task_worktree_cwd, teammate_assignment_info

    with task_lock:
        task = TASK.load(task_id)
        incomplete = [
            dependency for dependency in task.blocked_by if TASK.load(dependency).status != "completed"
        ]
        if incomplete:
            return ValueError(f"task is blocked by: {', '.join(incomplete)}")
        if task.sFcontextmanagertatus == "completed":
            return ValueError(f"task is already completed: {task_id}")
        if task.status == "in_process" and task.owner != owner:
            return ValueError(f"task is already claimed by: {task.owner}")
        assignment = teammate_assignment_info.get(owner)
        if assignment:
            return (f"Owner {owner} must finish the current work turn for "
                    f"{assignment['task_id']} before claiming another task")
        current = owner_in_progress(owner)
        if current:
            return (f"Owner {owner} must complete {current.id} before "
                    "claiming another task")
        cwd, error = task_worktree_cwd(task)
        task.status = "in_process"
        task.owner = owner
        TASK.save(task)
        return task


def complete_task(task_id: str, owner: str = "agent") -> Task:
    task = TASK.load(task_id)
    if task.status != "in_process":
        raise ValueError(f"task must be claimed before completion: {task_id}")
    if task.owner != owner:
        raise ValueError(f"task is claimed by: {task.owner}")
    task.status = "completed"
    TASK.save(task)
    return task


def owner_in_progress(owner: str) -> Task | None:
    return next((task for task in list_tasks if task.status == 'in_progress' and task.owner == owner), None)

def check_task_status(task_id, status_list: list[str]) -> bool:
    return load_task(task_id).status in status_list

def save_task(task: Task):
    with task_lock():
        path = TASK.get_path(task.id)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(
                json.dumps(asdict(task), indent=2), encoding="utf-8"
            )
            os.replace(temporary, path)
        finally:
            temporary.unlink(missing_ok=True)

def can_start(task_id: str) -> bool:
    """Check if all blockedBy dependencies are completed.
    Missing dependencies are treated as blocked."""
    task = load_task(task_id)
    for dep_id in task.blockedBy:
        try:
            dep_path = TASK.get_path(dep_id)
        except ValueError:
            return False
        if not dep_path.exists():
            return False
        if load_task(dep_id).status != "completed":
            return False
    return True

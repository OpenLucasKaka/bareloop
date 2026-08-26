import json
from dataclasses import asdict

from bareloop.settings import WORKDIR
from bareloop.task_system.model import Task, TaskStore

TASK = TaskStore(WORKDIR / ".bareloop" / "tasks")


def create_task(
    subject: str,
    description: str = "",
    blocked_by: list[str] | None = None,
) -> Task:
    return TASK.create(subject, description, blocked_by)


def list_tasks() -> list[Task]:
    return TASK.list()


def get_task(task_id: str) -> str:
    return json.dumps(asdict(TASK.load(task_id)), indent=2)


def claim_task(task_id: str, owner: str = "agent") -> Task:
    task = TASK.load(task_id)
    incomplete = [
        dependency for dependency in task.blocked_by if TASK.load(dependency).status != "completed"
    ]
    if incomplete:
        raise ValueError(f"task is blocked by: {', '.join(incomplete)}")
    if task.status == "completed":
        raise ValueError(f"task is already completed: {task_id}")
    if task.status == "in_process" and task.owner != owner:
        raise ValueError(f"task is already claimed by: {task.owner}")
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

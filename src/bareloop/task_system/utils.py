from __future__ import annotations

import json
from dataclasses import asdict

from bareloop.task_system.model import TASK_DIR, Task, TaskStore, task_lock

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
    return json.dumps(asdict(load_task(task_id)), indent=2)


def _validate_owner(owner: str) -> None:
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner must be a non-empty string")


def claim_task(task_id: str, owner: str = "agent") -> Task:
    from bareloop.worktree.index import task_worktree_cwd, teammate_assignment_info

    _validate_owner(owner)
    with task_lock():
        task = TASK.load(task_id)
        incomplete: list[str] = []
        for dependency in task.blocked_by:
            try:
                ready = TASK.load(dependency).status == "completed"
            except FileNotFoundError:
                ready = False
            if not ready:
                incomplete.append(dependency)
        if incomplete:
            raise ValueError(f"task is blocked by: {', '.join(incomplete)}")
        if task.status == "completed":
            raise ValueError(f"task is already completed: {task_id}")
        if task.status == "in_process" and task.owner != owner:
            raise ValueError(f"task is already claimed by: {task.owner}")

        assignment = teammate_assignment_info.get(owner)
        if assignment and assignment.get("task_id") != task_id:
            current = owner_in_progress(owner)
            if current is not None:
                raise ValueError(
                    f"Owner {owner} must complete {current.id} before claiming another task"
                )
            raise ValueError(
                f"Owner {owner} must finish the current work turn for "
                f"{assignment.get('task_id')} before claiming another task"
            )

        current = owner_in_progress(owner)
        if current is not None and current.id != task_id:
            raise ValueError(
                f"Owner {owner} must complete {current.id} before claiming another task"
            )

        cwd, error = task_worktree_cwd(task)
        if error or cwd is None:
            raise ValueError(error or f"Task {task_id} has no usable working directory")

        if task.status == "pending":
            task.status = "in_process"
            task.owner = owner
            TASK.save(task)
        teammate_assignment_info[owner] = {
            "task_id": task.id,
            "cwd": cwd.resolve(),
        }
        return task


def complete_task(task_id: str, owner: str = "agent") -> Task:
    _validate_owner(owner)
    with task_lock():
        task = TASK.load(task_id)
        if task.status != "in_process":
            raise ValueError(f"task must be claimed before completion: {task_id}")
        if task.owner != owner:
            raise ValueError(f"task is claimed by: {task.owner}")
        task.status = "completed"
        TASK.save(task)
        return task


def owner_in_progress(owner: str) -> Task | None:
    _validate_owner(owner)
    return next(
        (task for task in list_tasks() if task.status == "in_process" and task.owner == owner),
        None,
    )


def check_task_status(task_id: str, status_list: list[str]) -> bool:
    return load_task(task_id).status in status_list


def save_task(task: Task) -> None:
    TASK.save(task)


def can_start(task_id: str) -> bool:
    """Return whether all dependencies exist and have completed."""
    task = load_task(task_id)
    for dependency in task.blocked_by:
        try:
            if load_task(dependency).status != "completed":
                return False
        except (FileNotFoundError, ValueError):
            return False
    return True

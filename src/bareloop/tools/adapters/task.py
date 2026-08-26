from bareloop.task_system import claim_task, complete_task, create_task, get_task, list_tasks


def run_create_task(
    subject: str,
    description: str = "",
    blockedBy: list[str] | None = None,
) -> str:
    task = create_task(subject, description, blockedBy)
    dependencies = f" (blockedBy: {', '.join(task.blocked_by)})" if task.blocked_by else ""
    print(f"Created {task.id}: {task.subject}{dependencies}")
    return f"Created {task.id}: {task.subject}{dependencies}"


def run_list_tasks() -> str:
    tasks = list_tasks()
    if not tasks:
        return "No tasks"
    markers = {"pending": "[ ]", "in_process": "[>]", "completed": "[x]"}
    return "\n".join(
        f"{markers[task.status]} {task.id}: {task.subject} "
        f"(blocked_by={task.blocked_by}, owner={task.owner})"
        for task in tasks
    )


def run_get_task(task_id: str) -> str:
    return get_task(task_id)


def run_claim_task(task_id: str) -> str:
    task = claim_task(task_id, owner="agent")
    print(f"Claimed {task.id}: {task.subject}")
    return f"Claimed {task.id}: {task.subject}"


def run_complete_task(task_id: str) -> str:
    task = complete_task(task_id, owner="agent")
    print(f"Completed {task.id}: {task.subject}")
    return f"Completed {task.id}: {task.subject}"

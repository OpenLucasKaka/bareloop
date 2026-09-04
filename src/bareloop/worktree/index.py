from __future__ import annotations

import re
import subprocess
from pathlib import Path

from bareloop.settings import WORKDIR
from bareloop.task_system import (
    Task,
    TaskStore,
    list_tasks,
    load_task,
    save_task,
    task_lock,
)
from bareloop.task_system import model as task_model

WORKTREE_DIR = (WORKDIR / ".bareloop" / ".worktrees").resolve()
WORKTREE_DIR_MATCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
teammate_assignment_info: dict[str, dict[str, object]] = {}


def validate_worktree_name(name: str) -> str | None:
    if not isinstance(name, str) or WORKTREE_DIR_MATCH.fullmatch(name) is None:
        return f"Invalid worktree name: {name}"
    if ".." in name:
        return "Invalid worktree name: cannot contain '..'"
    return None


def _run_git(args: list[str], cwd: Path | None = None) -> tuple[bool, str]:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return False, f"{type(exc).__name__}: {exc}"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def _resolve_worktree_path(name: str) -> Path:
    error = validate_worktree_name(name)
    if error:
        raise ValueError(error)
    path = (WORKTREE_DIR / name).resolve()
    if not path.is_relative_to(WORKTREE_DIR) or path == WORKTREE_DIR:
        raise ValueError(f"Invalid worktree name: {name}")
    return path


def _create_worktree_branch(name: str) -> str:
    return f"wt/{name}"


def _read_registered_worktrees() -> tuple[dict[Path, dict[str, str]], str | None]:
    ok, output = _run_git(["worktree", "list", "--porcelain"])
    if not ok:
        return {}, f"cannot read Git worktree registry: {output}"
    entries: dict[Path, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in [*output.splitlines(), ""]:
        if not line:
            raw_path = current.get("worktree")
            if raw_path:
                entries[Path(raw_path).resolve()] = current
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return entries, None


def _read_registered_worktree(name: str) -> tuple[Path | None, str | None]:
    try:
        path = _resolve_worktree_path(name)
    except ValueError as exc:
        return None, str(exc)
    entries, error = _read_registered_worktrees()
    if error:
        return None, error
    if path not in entries:
        return None, f"Worktree {name} not found"
    if not path.is_dir():
        return None, f"worktree '{name}' is missing at {path}"
    expected_branch = f"refs/heads/{_create_worktree_branch(name)}"
    if entries[path].get("branch") != expected_branch:
        return None, (
            f"worktree '{name}' is not registered on expected branch "
            f"'{_create_worktree_branch(name)}'"
        )
    return path, None


def task_worktree_cwd(task: Task) -> tuple[Path | None, str | None]:
    if not task.worktree:
        return WORKDIR.resolve(), None
    return _read_registered_worktree(task.worktree)


def assignment_cwd(owner: str) -> Path:
    if not isinstance(owner, str) or not owner.strip():
        raise ValueError("owner must be a non-empty string")
    with task_lock():
        assignment = teammate_assignment_info.get(owner)
        if assignment is None:
            raise FileNotFoundError(f"Owner {owner} has no task assignment")

        task_id = assignment.get("task_id")
        if not isinstance(task_id, str):
            raise ValueError(f"Owner {owner} has an invalid task assignment")
        task = load_task(task_id)
        if task.owner != owner:
            raise ValueError(f"Task {task.id} is not owned by {owner}")
        if task.status not in {"in_process", "completed"}:
            raise ValueError(f"Task {task.id} is not in process or completed")

        cwd, error = task_worktree_cwd(task)
        if error or cwd is None:
            raise ValueError(error or f"Task {task.id} has no usable working directory")
        raw_assignment_cwd = assignment.get("cwd")
        if not isinstance(raw_assignment_cwd, (str, Path)):
            raise ValueError(f"Owner {owner} has an invalid assignment cwd")
        if cwd.resolve() != Path(raw_assignment_cwd).resolve():
            raise ValueError(f"Assignment cwd changed for task {task.id}")
        return cwd.resolve()


def _rollback_created_worktree(path: Path, branch: str) -> list[str]:
    errors: list[str] = []
    ok, output = _run_git(["worktree", "remove", "--force", str(path)])
    if not ok:
        errors.append(f"worktree removal failed: {output}")
    branch_exists, _ = _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
    if branch_exists:
        ok, output = _run_git(["branch", "-D", branch])
        if not ok:
            errors.append(f"branch removal failed: {output}")
    return errors


def create_worktree(name: str, task_id: str) -> str:
    error = validate_worktree_name(name)
    if error:
        return f"Error: {error}"
    try:
        path = _resolve_worktree_path(name)
        task_path = TaskStore(task_model.TASK_DIR).get_path(task_id)
    except (TypeError, ValueError) as exc:
        return f"Error: {exc}"
    branch = _create_worktree_branch(name)

    with task_lock():
        if not task_path.exists():
            return f"Error: Task {task_id} not found"
        try:
            task = load_task(task_id)
        except FileNotFoundError:
            return f"Error: Task {task_id} not found"
        if task.status != "pending" or task.owner is not None:
            return f"Error: Task {task_id} must be pending and unowned"
        if task.worktree:
            return f"Error: Task {task_id} already uses worktree '{task.worktree}'"
        if any(item.worktree == name for item in list_tasks() if item.id != task_id):
            return f"Error: Worktree '{name}' is already bound to another task"
        if path.exists():
            return f"Error: Worktree path already exists: {path}"

        ok, root = _run_git(["rev-parse", "--show-toplevel"])
        if not ok or Path(root).resolve() != WORKDIR.resolve():
            return "Error: Working directory must be the root of a Git repository"
        ok, branch_check = _run_git(["check-ref-format", "--branch", branch])
        if not ok:
            return f"Error: Invalid worktree branch '{branch}': {branch_check}"
        exists, _ = _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
        if exists:
            return f"Error: Branch '{branch}' already exists"
        entries, registry_error = _read_registered_worktrees()
        if registry_error:
            return f"Error: {registry_error}"
        if path in entries:
            return f"Error: Worktree path is already registered: {path}"

        WORKTREE_DIR.mkdir(parents=True, exist_ok=True)
        ok, result = _run_git(["worktree", "add", "-b", branch, str(path), "HEAD"])
        if not ok:
            entries, registry_error = _read_registered_worktrees()
            branch_exists, _ = _run_git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"])
            artifacts: list[str] = []
            if path.exists():
                artifacts.append(f"checkout path '{path}'")
            if registry_error is None and path in entries:
                artifacts.append("registered Git worktree")
            if branch_exists:
                artifacts.append(f"branch '{branch}'")
            if artifacts:
                return (
                    "Partial operation: git worktree add reported an error after leaving "
                    f"{', '.join(artifacts)}. Task {task_id} remains unbound. "
                    f"Git error: {result}"
                )
            return f"Git error: {result}"

        try:
            task.worktree = name
            save_task(task)
        except Exception as exc:
            task.worktree = None
            rollback_errors = _rollback_created_worktree(path, branch)
            if rollback_errors:
                return (
                    f"Partial operation: Could not bind worktree '{name}' to task "
                    f"{task_id}: {exc}. Rollback needs manual recovery: "
                    f"{'; '.join(rollback_errors)}"
                )
            return (
                f"Error: Could not bind worktree '{name}' to task {task_id}: {exc}. "
                "Created Git worktree and branch were rolled back."
            )

    print(f"  \033[33m[worktree] created: {name} at {path}\033[0m")
    return f"Worktree '{name}' created at {path} for task {task_id}"


def get_agent_cwd() -> tuple[Path | None, str | None]:
    try:
        return assignment_cwd("agent"), None
    except (FileNotFoundError, RuntimeError, ValueError) as exc:
        return None, f"Error: Invalid task assignment: {exc}"

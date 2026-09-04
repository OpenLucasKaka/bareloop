import re, subprocess
from bareloop.settings import WORKDIR
from pathlib import Path
from bareloop.task_system import (
    task_lock,
    owner_in_progress,
    check_task_status,
    TaskStore,
    load_task,
    list_tasks,
    save_task
)

WORKTREE_DIR = (WORKDIR / '.bareloop' / '.worktrees').resolve()
WORKTREE_DIR_MATCH = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$")
teammate_assignment_info: dict[str, dict[str, object]] = {}


def validate_worktree_name(self, name: str) -> str | None:
    if isinstance(name, str) or not WORKTREE_DIR_MATCH.fullmatch(name):
        return ValueError(f"Invalid worktree name: {name}")
    if ".." in name:
        return ValueError(f"Invalid worktree name: can not contain ..")
    return None


def _run_git(args: list[str], cwd: Path | None = None):
    try:
        result = subprocess.run(
            args=['git', *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=60
        )
    except (OSError, subprocess.TimeoutExpired) as e:
        return False, f"{type(e).__name__}: {e}"
    output = (result.stdout + result.stderr).strip()
    return result.returncode == 0, output


def _resolve_worktree_path(name: str):
    path = (WORKTREE_DIR / name).resolve()
    if not path.is_relative_to(WORKTREE_DIR) or path == WORKTREE_DIR:
        raise ValueError(f"Invalid worktree name: {name}")
    return path


def _create_worktree_branch(name: str) -> str:
    return f"wt/{name}"


def _read_registered_worktrees():
    ok, output = _run_git(["worktree", "list", "--porcelain"])
    if not ok:
        return {}, f"cannot read Git worktree registry: {output}"
    entries: dict[Path, dict[str, str]] = {}
    current: dict[str, str] = {}
    for line in output.splitlines() + [""]:
        if not line:
            raw_path = current.get("worktree")
            if raw_path:
                entries[Path(raw_path).resolve()] = current
            current = {}
            continue
        key, _, value = line.partition(" ")
        current[key] = value
    return entries, None


def _read_registered_worktree(name: str):
    try:
        path = _resolve_worktree_path(name)
    except ValueError as e:
        return None, str(e)
    entries, error = _read_registered_worktrees()
    if error:
        return None, error
    if path not in entries:
        return None, f"Worktree {name} not found"
    if not path.is_dir():
        return None, f"worktree '{name}' is missing at {path}"
    expected_branch = f"refs/heads/{_create_worktree_branch(name)}"
    if entries[path].get("branch") != expected_branch:
        return None, (f"worktree '{name}' is not registered on expected "
                      f"branch '{_create_worktree_branch(name)}'")
    return path, None


def task_worktree_cwd(task):
    if not task.worktree:
        return WORKDIR, None
    path, error = _read_registered_worktree(task.worktree)
    return (path, error), error


def assignment_cwd(owner: str) -> Path:
    with task_lock:
        assignment = teammate_assignment_info.get(owner)
        task = owner_in_progress(owner)
        # 工作区信息丢失或者不存在(不需要在worktree中工作)
        if task and not assignment:
            cwd, error = task_worktree_cwd(task)
            if error:
                raise ValueError(error)
        # 存在任务 但是匹配不上
        if assignment.get("task_id") != task.id:
            raise RuntimeError(
                f"Owner {owner} is still assigned to "
                f"{assignment['task_id']}; cannot switch to {task.id}"
            )
        task = load_task(str(assignment["task_id"]))
        if not check_task_status(task.id, ['pending', 'completed']):
            raise ValueError(f"Task {task.id} is not pending or completed")
        cwd, error = task_worktree_cwd(task)
        if error:
            raise ValueError(error)
        if cwd.resolve() != Path(assignment["cwd"]).resolve():
            raise ValueError(f"Assignment cwd changed for task {task.id}")
        return cwd


def create_worktree(name: str, task_id: str):
    error = validate_worktree_name()
    if error:
        return f"Cannot create worktree: {error}"
    try:
        path = _resolve_worktree_path(name)
        task_path = TaskStore.get_path(task_id)
    except Exception as e:
        return f"Error:{e}"
    branch = _create_worktree_branch(name)

    with task_lock:
        if not task_path.exists():
            return f"Error: {task_id}不存在"
        task = load_task(task_id)
        if task.status != "pending" or task.owner is not None:
            return f"Error: Task {task_id} 必须是 pending 和 unowned"
        if task.worktree:
            return f"Error: Task {task_id} 已使用 worktree '{task.worktree}'"
        if any(t.worktree == name for t in list_tasks() if t.id != task_id):
            return f"Error: Worktree '{name}' 已经关联其他task"
        if path.exists():
            return f"Error: Worktree path 已经存在: {path}"
        # 获取当前 Git 仓库主工作区的根目录
        ok, root = _run_git(["rev-parse", "--show-toplevel"])
        if not ok or Path(root).resolve() != WORKDIR.resolve():
            return "Error: Working directory must be the root of a Git repository"
        # 检查 branch 名称是否合法
        ok, branch_check = _run_git(["check-ref-format", "--branch", branch])
        if not ok:
            return f"Error: Invalid worktree branch '{branch}': {branch_check}"
        exists, _ = _run_git(["show-ref", "--verify", "--quiet",
                             f"refs/heads/{branch}"])
        if exists:
            return f"Error: Branch '{branch}' already exists"
        entries, registry_error = _read_registered_worktrees()
        if registry_error:
            return f"Error: {registry_error}"
        if path in entries:
            return f"Error: Worktree path is already registered: {path}"

        WORKTREE_DIR.mkdir(parents=True, exist_ok=True)
        ok, result = _run_git(["worktree", "add", "-b", branch,
                              str(path), "HEAD"])
        if not ok:
            entries, registry_error = _read_registered_worktrees()
            branch_exists, _ = _run_git(
                ["show-ref", "--verify", "--quiet", f"refs/heads/{branch}"]
            )
            artifacts = []
            if path.exists():
                artifacts.append(f"checkout path '{path}'")
            if registry_error is None and path in entries:
                artifacts.append("registered Git worktree")
            if branch_exists:
                artifacts.append(f"branch '{branch}'")
            if artifacts:
                return (
                    "Partial operation: git worktree add reported an error "
                    f"after leaving {', '.join(artifacts)}. Task {task_id} "
                    "remains unbound and no Git data was deleted. Run "
                    f"`git worktree list`, inspect '{path}' and '{branch}', "
                    "then keep or remove those artifacts manually after "
                    f"preserving any work. Git error: {result}"
                )
            return f"Git error: {result}"

        try:
            task.worktree = name
            save_task(task)
        except Exception as exc:
            return (f"Partial success: Worktree '{name}' was created at "
                    f"{path} on branch '{branch}', but task binding failed: "
                    f"{exc}. Git data was retained for manual recovery.")

    print(f"  \033[33m[worktree] created: {name} at {path}\033[0m")
    return f"Worktree '{name}' created at {path} for task {task_id}"

def get_agent_cwd():
    try:
        return assignment_cwd("agent"), None
    except (FileNotFoundError, ValueError) as exc:
        return None, f"Error: Invalid task assignment: {exc}"


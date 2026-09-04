from __future__ import annotations

import subprocess
import threading
from pathlib import Path

import pytest

from bareloop.task_system import model as task_model
from bareloop.task_system import utils as task_utils
from bareloop.task_system.model import Task, TaskStore
from bareloop.worktree import index as worktree_index


def _git(repo: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True, text=True)
    return result.stdout.strip()


@pytest.fixture
def git_task_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, TaskStore]:
    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init")
    _git(repo, "config", "user.email", "bareloop@example.invalid")
    _git(repo, "config", "user.name", "BareLoop Test")
    (repo / "README.md").write_text("test\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "initial")

    task_dir = repo / ".bareloop" / ".tasks"
    store = TaskStore(task_dir)
    monkeypatch.setattr(task_model, "TASK_DIR", task_dir)
    monkeypatch.setattr(task_model, "TASK_LOCK_PATH", task_dir / ".lock")
    monkeypatch.setattr(task_model, "_task_store_state", threading.local())
    monkeypatch.setattr(task_utils, "TASK", store)
    monkeypatch.setattr(worktree_index, "WORKDIR", repo.resolve())
    monkeypatch.setattr(
        worktree_index, "WORKTREE_DIR", (repo / ".bareloop" / ".worktrees").resolve()
    )
    real_run_git = worktree_index._run_git
    monkeypatch.setattr(
        worktree_index,
        "_run_git",
        lambda args, cwd=None: real_run_git(args, cwd=repo if cwd is None else cwd),
    )
    worktree_index.teammate_assignment_info.clear()
    yield repo, store
    worktree_index.teammate_assignment_info.clear()


@pytest.mark.parametrize("name", ["", ".hidden", "../escape", "two/names", "a..b", 7])
def test_validate_worktree_name_rejects_unsafe_names(name: object) -> None:
    error = worktree_index.validate_worktree_name(name)  # type: ignore[arg-type]
    assert error is not None
    assert "Invalid worktree name" in error


@pytest.mark.parametrize("name", ["feature", "feature-1", "A_b.c"])
def test_validate_worktree_name_accepts_safe_names(name: str) -> None:
    assert worktree_index.validate_worktree_name(name) is None


def test_task_worktree_cwd_returns_a_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    task = Task("task_1234abcd", "task", "", None, [], "pending")
    monkeypatch.setattr(worktree_index, "WORKDIR", tmp_path.resolve())

    cwd, error = worktree_index.task_worktree_cwd(task)

    assert cwd == tmp_path.resolve()
    assert isinstance(cwd, Path)
    assert error is None

    checkout = tmp_path / "checkout"
    checkout.mkdir()
    task.worktree = "feature"
    monkeypatch.setattr(worktree_index, "_read_registered_worktree", lambda _name: (checkout, None))
    cwd, error = worktree_index.task_worktree_cwd(task)
    assert cwd == checkout
    assert isinstance(cwd, Path)
    assert error is None


def test_create_worktree_binds_task_in_real_git_repository(
    git_task_store: tuple[Path, TaskStore],
) -> None:
    repo, _store = git_task_store
    task = task_utils.create_task("isolated")

    result = worktree_index.create_worktree("feature", task.id)

    checkout = repo / ".bareloop" / ".worktrees" / "feature"
    assert result == f"Worktree 'feature' created at {checkout} for task {task.id}"
    assert checkout.is_dir()
    assert task_utils.load_task(task.id).worktree == "feature"
    assert _git(repo, "branch", "--show-current") != "wt/feature"
    assert "wt/feature" in _git(repo, "branch", "--list", "wt/feature")


def test_create_worktree_returns_error_for_invalid_or_missing_task(
    git_task_store: tuple[Path, TaskStore],
) -> None:
    assert worktree_index.create_worktree("../escape", "task_deadbeef").startswith(
        "Error: Invalid worktree name"
    )
    assert worktree_index.create_worktree("safe", "task_deadbeef") == (
        "Error: Task task_deadbeef not found"
    )


def test_create_worktree_rolls_back_if_task_binding_fails(
    git_task_store: tuple[Path, TaskStore], monkeypatch: pytest.MonkeyPatch
) -> None:
    repo, _store = git_task_store
    task = task_utils.create_task("rollback")

    def fail_save(_task: Task) -> None:
        raise OSError("disk full")

    monkeypatch.setattr(worktree_index, "save_task", fail_save)
    result = worktree_index.create_worktree("rollback", task.id)

    checkout = repo / ".bareloop" / ".worktrees" / "rollback"
    assert result.startswith("Error: Could not bind worktree 'rollback'")
    assert not checkout.exists()
    assert _git(repo, "branch", "--list", "wt/rollback") == ""
    assert task_utils.load_task(task.id).worktree is None

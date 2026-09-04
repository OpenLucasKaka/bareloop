from __future__ import annotations

import json
import threading
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from bareloop.task_system import model as task_model
from bareloop.task_system import utils as task_utils
from bareloop.task_system.model import Task, TaskStore
from bareloop.worktree import index as worktree_index


@pytest.fixture
def task_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> TaskStore:
    task_dir = tmp_path / ".bareloop" / ".tasks"
    store = TaskStore(task_dir)
    monkeypatch.setattr(task_model, "TASK_DIR", task_dir)
    monkeypatch.setattr(task_model, "TASK_LOCK_PATH", task_dir / ".lock")
    monkeypatch.setattr(task_model, "_task_store_state", threading.local())
    monkeypatch.setattr(task_utils, "TASK", store)
    monkeypatch.setattr(worktree_index, "WORKDIR", tmp_path.resolve())
    worktree_index.teammate_assignment_info.clear()
    yield store
    worktree_index.teammate_assignment_info.clear()


def test_task_lock_is_a_nested_context_manager(
    task_store: TaskStore, monkeypatch: pytest.MonkeyPatch
) -> None:
    events: list[int] = []
    monkeypatch.setattr(task_model.fcntl, "flock", lambda _fd, operation: events.append(operation))

    with task_model.task_lock():
        assert events == [task_model.fcntl.LOCK_EX]
        with task_model.task_lock():
            assert events == [task_model.fcntl.LOCK_EX]
        assert events == [task_model.fcntl.LOCK_EX]

    assert events == [task_model.fcntl.LOCK_EX, task_model.fcntl.LOCK_UN]
    assert not hasattr(task_model._task_store_state, "depth")


def test_store_create_save_load_and_list(task_store: TaskStore) -> None:
    first = task_store.create("first")
    second = task_store.create("second", "details", [first.id, first.id])

    assert first.worktree is None
    assert second.blocked_by == [first.id]
    assert [task.id for task in task_store.list()] == sorted([first.id, second.id])

    second.subject = "updated"
    second.status = "in_process"
    second.owner = "worker"
    task_store.save(second)

    assert task_store.load(second.id) == second
    assert not list(task_store.directory.glob("*.tmp"))


def test_store_loads_legacy_json_without_worktree(task_store: TaskStore) -> None:
    task_id = "task_1234abcd"
    path = task_store.get_path(task_id, create_root=True)
    path.write_text(
        json.dumps(
            {
                "id": task_id,
                "subject": "legacy",
                "description": "",
                "owner": None,
                "blocked_by": [],
                "status": "pending",
            }
        ),
        encoding="utf-8",
    )

    assert task_store.load(task_id).worktree is None


def test_store_uses_its_own_directory_and_validates_errors(
    task_store: TaskStore, tmp_path: Path
) -> None:
    assert task_store.get_path("task_00000000").is_relative_to(tmp_path)
    with pytest.raises(ValueError, match="Invalid task id"):
        task_store.get_path("../escape")
    with pytest.raises(ValueError, match="subject"):
        task_store.create("  ")
    with pytest.raises(ValueError, match="dependency does not exist"):
        task_store.create("blocked", blocked_by=["task_deadbeef"])
    with pytest.raises(FileNotFoundError):
        task_store.load("task_deadbeef")

    invalid = Task("task_00000000", "bad", "", None, [], "in_progress")
    with pytest.raises(ValueError, match="Invalid task status"):
        task_store.save(invalid)


def test_blocked_by_and_error_contracts(task_store: TaskStore) -> None:
    dependency = task_utils.create_task("dependency")
    blocked = task_utils.create_task("blocked", blocked_by=[dependency.id])

    assert task_utils.can_start(blocked.id) is False
    with pytest.raises(ValueError, match=f"task is blocked by: {dependency.id}"):
        task_utils.claim_task(blocked.id, "worker")
    with pytest.raises(ValueError, match="must be claimed"):
        task_utils.complete_task(blocked.id, "worker")

    dependency.status = "completed"
    task_utils.save_task(dependency)
    assert task_utils.can_start(blocked.id) is True


def test_claim_creates_assignment_lease_and_uses_in_process_status(
    task_store: TaskStore, tmp_path: Path
) -> None:
    task = task_utils.create_task("claim me")

    claimed = task_utils.claim_task(task.id, "worker")

    assert claimed.status == "in_process"
    assert claimed.owner == "worker"
    assert task_utils.owner_in_progress("worker") == claimed
    assert worktree_index.teammate_assignment_info["worker"] == {
        "task_id": task.id,
        "cwd": tmp_path.resolve(),
    }
    assert worktree_index.assignment_cwd("worker") == tmp_path.resolve()


def test_owner_cannot_claim_a_second_task(task_store: TaskStore) -> None:
    first = task_utils.create_task("first")
    second = task_utils.create_task("second")
    task_utils.claim_task(first.id, "worker")

    with pytest.raises(ValueError, match=f"must complete {first.id}"):
        task_utils.claim_task(second.id, "worker")

    assert task_utils.load_task(second.id).status == "pending"


def test_claim_is_atomic_between_competing_owners(task_store: TaskStore) -> None:
    task = task_utils.create_task("one owner")
    barrier = threading.Barrier(2)

    def claim(owner: str) -> Task | Exception:
        barrier.wait()
        try:
            return task_utils.claim_task(task.id, owner)
        except Exception as exc:  # noqa: BLE001 - result is asserted by the test
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, ["one", "two"]))

    assert sum(isinstance(result, Task) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    persisted = task_utils.load_task(task.id)
    assert persisted.status == "in_process"
    assert persisted.owner in {"one", "two"}


def test_complete_is_atomic_and_keeps_lease_until_turn_boundary(task_store: TaskStore) -> None:
    task = task_utils.create_task("complete once")
    task_utils.claim_task(task.id, "worker")
    barrier = threading.Barrier(2)

    def complete() -> Task | Exception:
        barrier.wait()
        try:
            return task_utils.complete_task(task.id, "worker")
        except Exception as exc:  # noqa: BLE001 - result is asserted by the test
            return exc

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _unused: complete(), range(2)))

    assert sum(isinstance(result, Task) for result in results) == 1
    assert sum(isinstance(result, ValueError) for result in results) == 1
    assert task_utils.load_task(task.id).status == "completed"
    assert worktree_index.assignment_cwd("worker") == worktree_index.WORKDIR.resolve()


def test_assignment_cwd_rejects_missing_or_changed_lease(task_store: TaskStore) -> None:
    with pytest.raises(FileNotFoundError, match="no task assignment"):
        worktree_index.assignment_cwd("worker")

    task = task_utils.create_task("leased")
    task_utils.claim_task(task.id, "worker")
    worktree_index.teammate_assignment_info["worker"]["cwd"] = task_store.directory

    with pytest.raises(ValueError, match="Assignment cwd changed"):
        worktree_index.assignment_cwd("worker")

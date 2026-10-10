from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any

from bareloop.eval.swebench import (
    SWEBenchInstance,
    extract_git_patch,
    format_swebench_prompt,
    load_swebench_instances,
    run_swebench_instance,
    run_swebench_suite,
)
from bareloop.eval.swebench.__main__ import main as swebench_cli_main


def _init_git_repo(path: Path) -> str:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "config", "user.name", "Test User"], cwd=path, check=True, capture_output=True
    )
    subprocess.run(
        ["git", "config", "user.email", "test@example.com"],
        cwd=path,
        check=True,
        capture_output=True,
    )
    (path / "file.py").write_text("def hello():\n    return False\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=path, check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=path, check=True, capture_output=True, text=True
    ).stdout.strip()
    return commit


def test_load_sample_instances() -> None:
    sample_path = Path("evals/swebench/sample_instances.jsonl")
    assert sample_path.exists()
    instances = load_swebench_instances(sample_path)
    assert len(instances) == 2
    assert instances[0].instance_id == "psf__requests-863"
    assert instances[0].repo == "psf/requests"
    assert "Allow passing hooks" in instances[0].problem_statement
    assert instances[1].instance_id == "pallets__flask-4992"


def test_load_json_array(tmp_path: Path) -> None:
    json_path = tmp_path / "dataset.json"
    data = [
        {
            "instance_id": "test__test-1",
            "repo": "test/test",
            "base_commit": "abc1234",
            "problem_statement": "Fix the crash",
        }
    ]
    json_path.write_text(json.dumps(data), encoding="utf-8")
    instances = load_swebench_instances(json_path)
    assert len(instances) == 1
    assert instances[0].instance_id == "test__test-1"
    assert instances[0].base_commit == "abc1234"


def test_format_swebench_prompt() -> None:
    instance = SWEBenchInstance(
        instance_id="demo__demo-1",
        repo="demo/demo",
        base_commit="commit123",
        problem_statement="Calculation error in sum()",
        hints_text="Check math.py",
    )
    prompt = format_swebench_prompt(instance)
    assert "demo/demo" in prompt
    assert "commit123" in prompt
    assert "Calculation error in sum()" in prompt
    assert "Check math.py" in prompt


def test_extract_git_patch(tmp_path: Path) -> None:
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    base_commit = _init_git_repo(repo_dir)

    # Modify existing file
    (repo_dir / "file.py").write_text("def hello():\n    return True\n", encoding="utf-8")
    # Add new untracked file
    (repo_dir / "new_file.py").write_text("# new file\n", encoding="utf-8")

    patch = extract_git_patch(repo_dir, base_commit=base_commit)
    assert "diff --git a/file.py b/file.py" in patch
    assert "+    return True" in patch
    assert "diff --git a/new_file.py b/new_file.py" in patch


class _DummyMockSession:
    def __init__(self, workdir: Path, model: str | None = None, **kwargs: Any) -> None:
        self.workdir = workdir
        self.model = model or "mock-model"

    def run(self, prompt: str) -> Any:
        # Simulate agent fixing file.py
        target = self.workdir / "file.py"
        if target.exists():
            target.write_text("def hello():\n    return True\n", encoding="utf-8")

        class _MockResult:
            completed = True
            rounds = 2
            tool_calls = 3
            error = None

        return _MockResult()


def test_run_swebench_instance(tmp_path: Path) -> None:
    base_repo = tmp_path / "base_repo"
    base_repo.mkdir()
    base_commit = _init_git_repo(base_repo)

    instance = SWEBenchInstance(
        instance_id="test__mock-1",
        repo="test/repo",
        base_commit=base_commit,
        problem_statement="Return True instead of False",
    )

    result, prediction = run_swebench_instance(
        instance,
        base_repo_dir=base_repo,
        model="mock-model-v1",
        session_factory=lambda workdir, **kwargs: _DummyMockSession(workdir, **kwargs),
    )

    assert result.completed is True
    assert result.instance_id == "test__mock-1"
    assert result.model_name_or_path == "mock-model-v1"
    assert "def hello():" in prediction.model_patch
    assert "+    return True" in prediction.model_patch


def test_run_swebench_suite_and_cli(tmp_path: Path) -> None:
    base_repo = tmp_path / "base_repo"
    base_repo.mkdir()
    base_commit = _init_git_repo(base_repo)

    dataset_file = tmp_path / "dataset.jsonl"
    instance = SWEBenchInstance(
        instance_id="test__mock-1",
        repo="test/repo",
        base_commit=base_commit,
        problem_statement="Return True instead of False",
    )
    dataset_file.write_text(
        json.dumps(
            {
                "instance_id": instance.instance_id,
                "repo": instance.repo,
                "base_commit": instance.base_commit,
                "problem_statement": instance.problem_statement,
            }
        )
        + "\n",
        encoding="utf-8",
    )

    out_dir = tmp_path / "output"
    summary = run_swebench_suite(
        [instance],
        base_repo_dir=base_repo,
        output_dir=out_dir,
        model="mock-model",
        session_factory=lambda workdir, **kwargs: _DummyMockSession(workdir, **kwargs),
    )

    assert summary["total_instances"] == 1
    assert summary["patches_generated"] == 1
    pred_path = out_dir / "predictions.jsonl"
    assert pred_path.exists()
    lines = pred_path.read_text(encoding="utf-8").strip().splitlines()
    assert len(lines) == 1
    pred_data = json.loads(lines[0])
    assert pred_data["instance_id"] == "test__mock-1"
    assert "+    return True" in pred_data["model_patch"]

    # Test CLI execution with filter and limit
    cli_out_dir = tmp_path / "cli_output"
    rc = swebench_cli_main(
        [
            "--dataset-path",
            str(dataset_file),
            "--repo-dir",
            str(base_repo),
            "--output-dir",
            str(cli_out_dir),
            "--instance-id",
            "non-existent-id",
        ]
    )
    assert rc == 0

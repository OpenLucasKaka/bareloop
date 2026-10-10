from __future__ import annotations

import json
import shutil
import subprocess
from collections.abc import Callable, Sequence
from pathlib import Path
from tempfile import TemporaryDirectory
from time import perf_counter
from typing import Any

from bareloop.eval.swebench.models import (
    SWEBenchInstance,
    SWEBenchPrediction,
    SWEBenchRunResult,
    load_swebench_instances,
)
from bareloop.session import AgentSession

_SWE_SYSTEM_PROMPT = (
    "You are an expert software engineer resolving issues in a code repository. "
    "Investigate the codebase carefully, reproduce or locate the bug, and apply the exact fixes "
    "needed using your available tools. Do not commit your changes to git."
)

_DEFAULT_SWE_TOOLS = frozenset({"read", "write", "edit", "glob", "bash"})


def format_swebench_prompt(instance: SWEBenchInstance) -> str:
    parts = [
        f"You are working on the repository '{instance.repo}' at commit '{instance.base_commit}'.",
        "An issue has been reported with the following description:",
        "",
        "<issue>",
        instance.problem_statement.strip(),
        "</issue>",
    ]
    if instance.hints_text:
        parts.extend(
            [
                "",
                "<hints>",
                instance.hints_text.strip(),
                "</hints>",
            ]
        )
    parts.extend(
        [
            "",
            "Please investigate the files, identify the bug, and use file editing tools to fix it.",
            "Make clean, focused changes. Do not make git commits.",
        ]
    )
    return "\n".join(parts)


def extract_git_patch(repo_dir: Path, base_commit: str | None = None) -> str:
    target = repo_dir.resolve()
    # Track untracked files with intent-to-add so git diff captures them
    try:
        status_proc = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=target,
            capture_output=True,
            text=True,
            check=False,
        )
        if status_proc.returncode == 0:
            for line in status_proc.stdout.splitlines():
                if line.startswith("?? "):
                    untracked_file = line[3:].strip()
                    subprocess.run(
                        ["git", "add", "-N", untracked_file],
                        cwd=target,
                        capture_output=True,
                        check=False,
                    )
    except OSError:
        pass

    diff_cmd = ["git", "diff"]
    if base_commit:
        diff_cmd.append(base_commit)

    try:
        diff_proc = subprocess.run(
            diff_cmd,
            cwd=target,
            capture_output=True,
            text=True,
            check=False,
        )
        if diff_proc.returncode == 0:
            return diff_proc.stdout
    except OSError:
        pass
    return ""


def setup_instance_workspace(
    instance: SWEBenchInstance,
    base_repo_dir: Path | str,
    target_workspace: Path | str,
) -> Path:
    src_repo = Path(base_repo_dir).resolve()
    dest_dir = Path(target_workspace).resolve()

    if not (src_repo / ".git").exists():
        raise ValueError(f"Base repository path is not a valid git repository: {src_repo}")

    dest_dir.mkdir(parents=True, exist_ok=True)
    # Clone locally for an isolated workspace
    clone_proc = subprocess.run(
        ["git", "clone", "--local", str(src_repo), str(dest_dir)],
        capture_output=True,
        text=True,
        check=False,
    )
    if clone_proc.returncode != 0:
        # Fallback to file copy if local clone fails
        shutil.copytree(src_repo, dest_dir, dirs_exist_ok=True)

    # Checkout base_commit
    checkout_proc = subprocess.run(
        ["git", "checkout", "-f", instance.base_commit],
        cwd=dest_dir,
        capture_output=True,
        text=True,
        check=False,
    )
    if checkout_proc.returncode != 0:
        raise RuntimeError(
            f"Failed to checkout commit {instance.base_commit} for {instance.instance_id}: "
            f"{checkout_proc.stderr}"
        )
    return dest_dir


def run_swebench_instance(
    instance: SWEBenchInstance,
    *,
    base_repo_dir: Path | str,
    model: str | None = None,
    max_rounds: int = 15,
    allowed_tool_names: frozenset[str] | set[str] | None = None,
    session_factory: Callable[..., Any] | None = None,
    workspace_dir: Path | str | None = None,
) -> tuple[SWEBenchRunResult, SWEBenchPrediction]:
    tools = allowed_tool_names if allowed_tool_names is not None else _DEFAULT_SWE_TOOLS

    def _execute(workdir: Path) -> tuple[SWEBenchRunResult, SWEBenchPrediction]:
        prompt = format_swebench_prompt(instance)
        started_at = perf_counter()

        if session_factory is not None:
            session = session_factory(workdir=workdir, model=model, max_rounds=max_rounds)
        else:
            from bareloop.settings import PRIMARY_MODEL, client, tokenizer

            chosen_model = model or PRIMARY_MODEL
            session = AgentSession(
                workdir=workdir,
                system_prompt=_SWE_SYSTEM_PROMPT,
                client_instance=client,
                model=chosen_model,
                tokenizer_instance=tokenizer,
                max_rounds=max_rounds,
                trace=None,
                allowed_tool_names=tools,
            )

        run_result = session.run(prompt)
        duration_ms = (perf_counter() - started_at) * 1000

        patch = extract_git_patch(workdir, base_commit=instance.base_commit)
        prediction = SWEBenchPrediction(
            instance_id=instance.instance_id,
            model_name_or_path=str(model or getattr(session, "model", "bareloop")),
            model_patch=patch,
        )
        result = SWEBenchRunResult(
            instance_id=instance.instance_id,
            completed=getattr(run_result, "completed", True),
            model_name_or_path=prediction.model_name_or_path,
            model_patch=patch,
            rounds=getattr(run_result, "rounds", 0),
            tool_calls=getattr(run_result, "tool_calls", 0),
            duration_ms=duration_ms,
            error=getattr(run_result, "error", None),
        )
        return result, prediction

    if workspace_dir is not None:
        ws = setup_instance_workspace(instance, base_repo_dir, workspace_dir)
        return _execute(ws)

    with TemporaryDirectory(prefix=f"bareloop_swe_{instance.instance_id}_") as temp_dir:
        ws = setup_instance_workspace(instance, base_repo_dir, Path(temp_dir))
        return _execute(ws)


def run_swebench_suite(
    instances: Sequence[SWEBenchInstance] | str | Path,
    *,
    base_repo_dir: Path | str,
    output_dir: Path | str,
    model: str | None = None,
    max_rounds: int = 15,
    allowed_tool_names: frozenset[str] | set[str] | None = None,
    session_factory: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    if isinstance(instances, (str, Path)):
        instances = load_swebench_instances(instances)

    out_path = Path(output_dir).resolve()
    out_path.mkdir(parents=True, exist_ok=True)
    predictions_file = out_path / "predictions.jsonl"
    report_file = out_path / "report.json"

    results: list[dict[str, Any]] = []
    patches_generated = 0

    with predictions_file.open("w", encoding="utf-8") as pred_f:
        for inst in instances:
            result, prediction = run_swebench_instance(
                inst,
                base_repo_dir=base_repo_dir,
                model=model,
                max_rounds=max_rounds,
                allowed_tool_names=allowed_tool_names,
                session_factory=session_factory,
            )
            pred_f.write(prediction.to_json_line() + "\n")
            pred_f.flush()
            if prediction.model_patch.strip():
                patches_generated += 1
            results.append(result.to_dict())

    summary = {
        "total_instances": len(instances),
        "patches_generated": patches_generated,
        "patch_generation_rate": (patches_generated / len(instances)) if instances else 0.0,
        "output_predictions": str(predictions_file),
        "results": results,
    }
    report_file.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding="utf-8")
    return summary

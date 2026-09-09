# Agent Eval v0 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a repeatable offline evaluation pipeline that runs BareLoop against fixed coding tasks and reports task success rate, duration, and tool usage without changing the existing CLI behavior.

**Architecture:** Keep `agent_loop()` as the procedural CLI facade and add `AgentSession.run()` as the object-oriented facade. Both call one shared loop implementation and return a structured `RunResult`. The Eval runner copies each case fixture into a temporary directory, runs an isolated session, executes deterministic graders, and writes a JSON report.

**Tech Stack:** Python 3.12, dataclasses, PyYAML, pytest, subprocess, tempfile, existing OpenAI-compatible client.

---

## File map

- Create `src/bareloop/session.py`: session configuration, structured result, and object-oriented API.
- Modify `src/bareloop/loop.py`: extract the shared execution path and remove result-only `print` behavior from the core.
- Modify `src/bareloop/tools/dispatcher.py`: bind filesystem and shell tools to the session workspace.
- Modify `src/bareloop/mian.py`: retain the procedural entry and print the returned result.
- Create `src/bareloop/eval/models.py`: parse and validate Eval case manifests.
- Create `src/bareloop/eval/graders.py`: deterministic command and file graders.
- Create `src/bareloop/eval/runner.py`: copy fixtures, execute sessions, aggregate results.
- Create `src/bareloop/eval/__main__.py`: `python -m bareloop.eval` command.
- Create `evals/cases/write-answer/`: smallest file-writing case.
- Create `evals/cases/fix-add/`: smallest code-repair case.
- Create `tests/test_session.py`: shared-core and isolation tests.
- Create `tests/test_eval.py`: manifest, grader, runner, and report tests.
- Modify `README.zh-CN.md`: document the Eval command and metric meaning.

### Task 1: Define the programmable Session contract

**Files:**
- Create: `src/bareloop/session.py`
- Test: `tests/test_session.py`

- [ ] **Step 1: Write failing tests for result shape and isolated messages**

```python
from pathlib import Path

from bareloop.session import AgentSession, RunResult


def test_run_result_exposes_eval_fields() -> None:
    result = RunResult(
        completed=True,
        final_output="done",
        messages=[],
        tool_calls=2,
        rounds=3,
        duration_ms=12.5,
        error=None,
    )
    assert result.completed is True
    assert result.tool_calls == 2


def test_sessions_do_not_share_messages(tmp_path: Path) -> None:
    first = AgentSession(workdir=tmp_path, system_prompt="system")
    second = AgentSession(workdir=tmp_path, system_prompt="system")
    first.messages.append({"role": "user", "content": "one"})
    assert second.messages == [{"role": "system", "content": "system"}]
```

- [ ] **Step 2: Run the tests and confirm the missing module failure**

Run: `uv run pytest tests/test_session.py -q`

Expected: collection fails because `bareloop.session` does not exist.

- [ ] **Step 3: Add the minimal data structures and Session shell**

```python
from dataclasses import dataclass
from pathlib import Path
from time import monotonic
from typing import Any

from bareloop.mode import AgentMode
from bareloop.settings import PRIMARY_MODEL, client, tokenizer
from bareloop.trace import TraceWriter


@dataclass
class RunResult:
    completed: bool
    final_output: str
    messages: list[dict[str, Any]]
    tool_calls: int
    rounds: int
    duration_ms: float
    error: str | None


class AgentSession:
    def __init__(
        self,
        *,
        workdir: Path,
        system_prompt: str,
        client_instance: Any = client,
        model: str | None = PRIMARY_MODEL,
        tokenizer_instance: Any = tokenizer,
        max_turns: int = 20,
        mode: AgentMode = AgentMode.NORMAL,
        trace: TraceWriter | None = None,
    ) -> None:
        if max_turns < 1:
            raise ValueError("max_turns must be greater than zero")
        self.workdir = workdir.resolve()
        self.client = client_instance
        self.model = model
        self.tokenizer = tokenizer_instance
        self.max_turns = max_turns
        self.mode = mode
        self.trace = trace or TraceWriter()
        self.messages: list[dict[str, Any]] = [
            {"role": "system", "content": system_prompt}
        ]

    def run(self, prompt: str) -> RunResult:
        from bareloop.loop import execute_agent_loop

        self.messages.append({"role": "user", "content": prompt})
        started_at = monotonic()
        result = execute_agent_loop(
            messages=self.messages,
            tw=self.trace,
            mode=self.mode,
            client_instance=self.client,
            model=self.model,
            tokenizer_instance=self.tokenizer,
            workdir=self.workdir,
            max_turns=self.max_turns,
        )
        result.duration_ms = (monotonic() - started_at) * 1000
        return result
```

- [ ] **Step 4: Run the narrow tests**

Run: `uv run pytest tests/test_session.py -q`

Expected: the result-shape and message-isolation tests pass; `run()` is exercised after Task 2.

- [ ] **Step 5: Commit this unit**

Run: `git add src/bareloop/session.py tests/test_session.py && git commit -m "feat: define programmable agent session"`

### Task 2: Make both entry paths share one execution core

**Files:**
- Modify: `src/bareloop/loop.py`
- Modify: `src/bareloop/mian.py`
- Modify: `tests/test_session.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Add a test proving the Class path returns the final model response**

Use a fake OpenAI response with no tool calls, a fake tokenizer returning an empty token list, and monkeypatch memory functions to no-ops. Assert `session.run("hello")` returns `completed=True` and `final_output="done"`.

- [ ] **Step 2: Run the test and confirm `execute_agent_loop` is missing**

Run: `uv run pytest tests/test_session.py -q`

Expected: failure naming `execute_agent_loop`.

- [ ] **Step 3: Extract the core in `loop.py`**

Rename the current private loop body to `execute_agent_loop(...)`. Add keyword-only parameters for `client_instance`, `model`, `tokenizer_instance`, `workdir`, and `max_turns`. Replace the global model call with:

```python
response = client_instance.chat.completions.create(
    model=model,
    messages=request_messages,
    tools=tool_schemas,
)
```

Count model rounds and return `RunResult` on every exit. A model exception returns `completed=False` with its exception message. Reaching `max_turns` returns `completed=False` with `error="max_turns_exceeded"`. Do not print inside `execute_agent_loop`.

- [ ] **Step 4: Preserve the existing function facade**

Keep `agent_loop(messages, tw, mode)` and have it call `execute_agent_loop` with settings defaults. Preserve cron acknowledgement and memory maintenance in this facade, then return the `RunResult`.

- [ ] **Step 5: Keep CLI output in `mian.py`**

Change `run_agent_turn_locked` to print `result.final_output` after `agent_loop` returns. Existing CLI users should observe the same text as before.

- [ ] **Step 6: Run focused and full tests**

Run: `uv run pytest tests/test_session.py tests/test_runtime.py tests/test_cli.py -q`

Expected: all selected tests pass.

Run: `uv run pytest -q`

Expected: the full suite passes.

- [ ] **Step 7: Commit this unit**

Run: `git add src/bareloop/loop.py src/bareloop/mian.py tests/test_session.py tests/test_runtime.py tests/test_cli.py && git commit -m "refactor: share agent execution core"`

### Task 3: Bind every Eval run to its temporary workspace

**Files:**
- Modify: `src/bareloop/tools/dispatcher.py`
- Modify: `src/bareloop/loop.py`
- Modify: `tests/test_runtime.py`

- [ ] **Step 1: Write a failing workspace injection test**

Register or monkeypatch a filesystem tool definition, call `dispatch_tool("write", {"path": "answer.txt", "content": "42"}, cwd=tmp_path)`, and assert the file appears under `tmp_path`, not the repository root.

- [ ] **Step 2: Run the test and confirm `cwd` is unsupported**

Run: `uv run pytest tests/test_runtime.py -q`

Expected: failure because `dispatch_tool` has no `cwd` argument.

- [ ] **Step 3: Inject cwd only into workspace-aware tools**

Add `cwd: Path | None = None` to `dispatch_tool`. When the selected tool is one of `bash`, `read`, `write`, `edit`, or `glob`, set `tool_arguments["cwd"] = cwd` unless the caller already supplied `cwd`. Do not add this argument to MCP or unrelated tools.

- [ ] **Step 4: Pass the Session workspace from the core loop**

Update the core dispatch call to pass `cwd=workdir`. The legacy facade passes `settings.WORKDIR`; `AgentSession` passes its own temporary case directory.

- [ ] **Step 5: Verify containment and regression behavior**

Run: `uv run pytest tests/test_runtime.py -q`

Expected: workspace injection, path-escape protection, and existing dispatcher tests pass.

- [ ] **Step 6: Commit this unit**

Run: `git add src/bareloop/tools/dispatcher.py src/bareloop/loop.py tests/test_runtime.py && git commit -m "feat: bind tool calls to session workspace"`

### Task 4: Add deterministic Eval cases and graders

**Files:**
- Create: `src/bareloop/eval/__init__.py`
- Create: `src/bareloop/eval/models.py`
- Create: `src/bareloop/eval/graders.py`
- Create: `evals/cases/write-answer/case.yaml`
- Create: `evals/cases/fix-add/case.yaml`
- Create: `evals/cases/fix-add/workspace/calculator.py`
- Create: `evals/cases/fix-add/workspace/test_calculator.py`
- Test: `tests/test_eval.py`

- [ ] **Step 1: Write manifest parsing and grader tests**

Cover missing `id`, empty `prompt`, unsupported grader type, exact file comparison, command exit code zero, and command timeout. Use `tmp_path`; do not call a real model.

- [ ] **Step 2: Run tests and confirm missing Eval modules**

Run: `uv run pytest tests/test_eval.py -q`

Expected: collection fails because `bareloop.eval` does not exist.

- [ ] **Step 3: Define the manifest model**

Use dataclasses and `yaml.safe_load`. Each case contains `id`, `prompt`, `workspace`, `timeout_seconds`, and a non-empty list of graders. Support only these graders in v0:

```yaml
graders:
  - type: file_equals
    path: answer.txt
    expected: "42\n"
  - type: command
    argv: ["{python}", "-m", "pytest", "-q"]
    timeout_seconds: 30
```

- [ ] **Step 4: Implement deterministic graders**

`file_equals` resolves the requested path inside the copied workspace and rejects path escape. `command` replaces `{python}` with `sys.executable`, runs with `shell=False`, captures output, enforces timeout, and passes only when return code is zero.

- [ ] **Step 5: Add the two seed cases**

`write-answer` asks the Agent to create `answer.txt` containing exactly `42` and uses `file_equals`. `fix-add` begins with `calculator.add(a, b)` incorrectly subtracting and uses pytest to require positive, negative, and zero inputs.

- [ ] **Step 6: Run grader tests**

Run: `uv run pytest tests/test_eval.py -q`

Expected: all parser and grader tests pass without API access.

- [ ] **Step 7: Commit this unit**

Run: `git add src/bareloop/eval evals tests/test_eval.py && git commit -m "feat: add deterministic eval cases and graders"`

### Task 5: Run the dataset and calculate task success rate

**Files:**
- Create: `src/bareloop/eval/runner.py`
- Create: `src/bareloop/eval/__main__.py`
- Modify: `tests/test_eval.py`
- Modify: `README.zh-CN.md`

- [ ] **Step 1: Write a runner test with a fake Session factory**

Use two temporary cases and a fake session that writes the expected file. Assert the original fixture is unchanged, each repeat receives a fresh copy, and the report contains total runs, passed runs, task success rate, duration, and error details.

- [ ] **Step 2: Run the test and confirm the runner is missing**

Run: `uv run pytest tests/test_eval.py -q`

Expected: failure importing the runner.

- [ ] **Step 3: Implement isolated case execution**

For every case and repeat, create a `TemporaryDirectory`, copy the fixture into it, instantiate `AgentSession(workdir=...)`, run the prompt, then execute all graders. Mark the run successful only when the Session completes and every grader passes.

- [ ] **Step 4: Implement report aggregation**

Write one JSON document containing `created_at`, `git_commit`, `model`, `repeat`, per-run results, and:

```python
task_success_rate = passed_runs / total_runs if total_runs else 0.0
```

Do not label this value `accuracy`; reserve that term for classification datasets with a unique label.

- [ ] **Step 5: Add the CLI**

Support:

```bash
uv run python -m bareloop.eval \
  --cases evals/cases \
  --repeat 1 \
  --output .bareloop/eval/latest.json
```

Exit zero when the runner itself completes, even if some cases fail. Exit non-zero only for invalid manifests, missing configuration, or runner infrastructure failure.

- [ ] **Step 6: Document the first real baseline run**

Add a concise README section explaining that `Pass@1` is passed runs divided by total runs, that model stochasticity requires `--repeat 3` before comparisons, and that unit tests and Eval measure different layers.

- [ ] **Step 7: Verify code quality and the first baseline**

Run: `uv run pytest -q`

Expected: the full suite passes.

Run: `uv run ruff check . && uv run ruff format --check .`

Expected: both commands exit zero.

Run the Eval command with configured API credentials. Expected: `.bareloop/eval/latest.json` contains two runs and a task success rate between `0.0` and `1.0`.

- [ ] **Step 8: Commit this unit**

Run: `git add src/bareloop/eval tests/test_eval.py README.zh-CN.md && git commit -m "feat: add offline agent eval runner"`

## Dataset expansion after v0 works

Expand from 2 to 10 cases only after the pipeline produces a valid report. Use three file operations, three bug fixes, two tool-error recovery tasks, one workspace-boundary task, and one long-context task. Keep every grader deterministic. Run one repeat during development and three repeats before comparing models, prompts, memory behavior, or compaction behavior.

## Completion gate

The work is complete when the old interactive CLI still behaves the same, `AgentSession` can run against an arbitrary temporary workspace, two seed cases run from one command, every run starts from a clean fixture, the JSON report records reproducibility metadata, and the full pytest and Ruff suites pass.

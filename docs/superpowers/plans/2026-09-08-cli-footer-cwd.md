# CLI Footer CWD Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Move the static workspace display from a per-turn hook print into the CLI bottom toolbar.

**Architecture:** `mian.py` owns terminal presentation and reads the immutable `WORKDIR` directly. `hook/hook.py` removes the presentation-only callback while retaining all behavioral lifecycle hooks.

**Tech Stack:** Python, prompt_toolkit formatted text, pytest, Ruff

---

### Task 1: Lock the footer and hook behavior

**Files:**
- Modify: `tests/test_runtime.py`

- [x] Add assertions that Normal and Goal toolbar fragments include `CWD: {WORKDIR}`.
- [x] Add an isolated hook-registry test that initializes hooks, triggers `PreUserPromptInput`, and asserts stdout remains empty.
- [x] Run `.venv/bin/python -m pytest -q tests/test_runtime.py -k 'cli_mode_prompts_keep_input_label_fixed or pre_user_prompt_hook'` and verify failure against the existing behavior.

### Task 2: Move CWD presentation into the toolbar

**Files:**
- Modify: `src/bareloop/mian.py`
- Modify: `src/bareloop/hook/hook.py`

- [x] Append `("class:hint", f" · {WORKDIR}")` to the existing toolbar fragments in `wait_for_cli_event()`.
- [x] Delete `context_inject_hook()` and stop registering it in `hook()`.
- [x] Re-run the focused tests and verify they pass.
- [x] Run the full pytest suite, Ruff checks, Ruff format check, and `py_compile` for the changed production files.
- [x] Do not commit; the workspace protocol requires explicit user authorization before committing design documents or code.

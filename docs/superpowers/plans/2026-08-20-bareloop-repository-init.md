# BareLoop Repository Initialization Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Initialize a reproducible Python repository for the BareLoop agent harness without migrating prototype code.

**Architecture:** Use a standard `src` package so imports exercise installed-package behavior. Keep runtime code empty except for package identity, and establish pytest and Ruff as the first quality gate.

**Tech Stack:** Python 3.12, uv, pytest, Ruff, Hatchling, Git

---

### Task 1: Repository metadata

**Files:**
- Create: `.gitignore`
- Create: `LICENSE`
- Create: `README.md`
- Create: `pyproject.toml`

- [ ] Initialize Git on branch `main`, then create working branch `codex/init-bareloop`.
- [ ] Define package metadata, Python 3.12 requirement, Hatchling build backend, and development dependencies for pytest and Ruff.
- [ ] Document the project positioning, installation command, development checks, and planned 17-mechanism scope.
- [ ] Add Python, uv, editor, test, build, environment, and BareLoop runtime artifacts to `.gitignore`.
- [ ] Add the MIT license with copyright owned by the BareLoop contributors.

### Task 2: Package smoke test using TDD

**Files:**
- Create: `tests/test_package.py`
- Create: `src/bareloop/__init__.py`

- [ ] Write a smoke test that imports `bareloop` and asserts `__version__ == "0.1.0"`.
- [ ] Run `uv run pytest tests/test_package.py -q` and verify it fails because the package does not exist.
- [ ] Create the minimal package with `__version__ = "0.1.0"`.
- [ ] Run `uv run pytest -q` and verify the test passes.

### Task 3: Repository verification

**Files:**
- Verify: all files in the repository

- [ ] Run `uv run ruff check .` and require a clean result.
- [ ] Run `uv run ruff format --check .` and require a clean result.
- [ ] Run `uv build` and require both source and wheel artifacts.
- [ ] Run `git status --short --branch` and confirm all initialization files are uncommitted on `codex/init-bareloop`.
- [ ] Do not commit; repository ownership and the first commit remain with the user.


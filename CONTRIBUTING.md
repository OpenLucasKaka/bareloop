# Contributing to BareLoop

Thank you for your interest in contributing to BareLoop!

## Development Setup

1. **Install Prerequisites**:
   Ensure you have Python 3.12+ and [uv](https://docs.astral.sh/uv/) installed.

2. **Clone and Install Dependencies**:
   ```bash
   git clone https://github.com/OpenLucasKaka/bareloop.git
   cd bareloop
   uv sync --all-groups
   ```

   To match CI, use Python 3.12: `uv sync --all-groups --python 3.12`.
   Install the repository's local checks with `uv run pre-commit install`.

3. **Environment Setup**:
   ```bash
   cp .env.example .env
   # Edit .env with your model API key and configuration
   ```

## Code Quality & Testing Standards

Before submitting a Pull Request, please ensure all checks pass:

- **Linting**:
  ```bash
  uv run ruff check .
  ```

- **Formatting**:
  ```bash
  uv run ruff format --check .
  ```

- **Test Suite**:
  ```bash
  uv run pytest
  ```

## Pull Request Guidelines

- Check the existing issues and pull requests before starting, and link the issue your PR addresses.
- Keep each PR focused on one change. Leave unrelated formatting, dependency updates, and refactors
  for separate PRs.
- Ensure new features or bug fixes include corresponding unit tests under `tests/`.
- Maintain backward compatibility for public APIs and tool registration.
- Write clear, concise commit messages.
- Describe what changed and why, list the checks you ran, and mention any failures or checks you
  could not run. Do not report a failing check as passing.

## Local Configuration and Credentials

Keep real API keys and endpoint credentials in your local `.env`, which Git ignores. Do not commit
that file, paste its values into an issue or PR, or include them in test fixtures and logs. Add new
configuration keys to `.env.example` with placeholders, never real credentials. Review your staged
changes with `git diff --cached` before committing.

Provider configuration is needed to run the agent interactively; it is not a requirement for
installing the project or running the checks above. Tests should mock model responses rather than
call a paid endpoint. For credential-related reports, follow [SECURITY.md](SECURITY.md).

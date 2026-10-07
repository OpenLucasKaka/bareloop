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

- Ensure new features or bug fixes include corresponding unit tests under `tests/`.
- Maintain backward compatibility for public APIs and tool registration.
- Write clear, concise commit messages.

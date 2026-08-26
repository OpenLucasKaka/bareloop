# BareLoop Repository Design

## Purpose

BareLoop is a transparent, OpenAI-compatible agent harness built from first
principles. The repository will host one maintainable runtime and a progressive
set of examples that eventually cover the 17 harness mechanisms studied in the
source course.

## Initial Scope

This initialization creates only the repository foundation:

- Python 3.12 project managed with `uv`
- `src/bareloop` package layout
- pytest and Ruff development tooling
- MIT license, README, and Git ignore rules
- one package smoke test

No code is migrated from the prototype during initialization.

## Architecture Direction

The runtime will grow behind stable module boundaries rather than recreating a
single monolithic script. Future modules will cover core loop and providers,
tools and policy, context and memory, task and asynchronous runtime, teams and
MCP, workflow orchestration, and goal completion.

The 17 lessons will be examples that consume the same runtime. They will not be
17 drifting copies of the production implementation.

## Quality Contract

New behavior is developed test-first. Provider-dependent tests use a fake
provider so the default suite runs offline. Configuration must not contain
private endpoints or credentials. Public APIs use type annotations, and Ruff
and pytest form the initial verification gate.

## Repository Identity

- Distribution: `bareloop`
- Import package: `bareloop`
- License: MIT
- Tagline: A transparent, OpenAI-compatible agent harness built from first principles.


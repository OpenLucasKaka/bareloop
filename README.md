# BareLoop

> A transparent, OpenAI-compatible agent harness built from first principles.

BareLoop is an educational and practical agent runtime that makes every harness
mechanism visible. It starts with the smallest useful model/tool loop and grows
into a complete runtime without hiding control flow behind an agent framework.

## Status

BareLoop is in the repository-foundation stage. The runtime API has not been
implemented yet.

## Design goals

- Keep the model/tool/result loop explicit and inspectable.
- Support OpenAI-compatible providers without coupling the runtime to one model.
- Make permissions, persistence, concurrency, and recovery host-owned concerns.
- Run the default test suite offline with deterministic fake providers.
- Teach each mechanism through progressive examples backed by one runtime.

## Planned capability path

1. Agent loop
2. Tool registry and dispatch
3. Permission policy
4. Lifecycle hooks
5. Todo planning
6. Isolated subagents
7. On-demand skills
8. Context compaction
9. Durable memory
10. Task dependency graph
11. Background tasks
12. Cron scheduling
13. Agent teams and worktree routing
14. MCP tool discovery
15. Integrated harness
16. Resumable workflows
17. Goal completion gate

## Development

Requirements: Python 3.12 or newer and [uv](https://docs.astral.sh/uv/).

```bash
uv sync
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv build
```

## License

BareLoop is released under the MIT License. See [LICENSE](LICENSE).


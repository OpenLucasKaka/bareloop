from __future__ import annotations


def spawn_subagent(query: str) -> str:
    from bareloop.subagent import spawn_subagent as _spawn

    return _spawn(query)


def spaw_subagent(query: str) -> str:
    """Backward-compatible alias for spawn_subagent."""
    return spawn_subagent(query)

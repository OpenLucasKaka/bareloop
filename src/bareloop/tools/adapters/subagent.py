from __future__ import annotations

from bareloop.subagent import spawn_subagent as _spawn_subagent


def run_spawn_subagent(query: str) -> str:
    return _spawn_subagent(query)

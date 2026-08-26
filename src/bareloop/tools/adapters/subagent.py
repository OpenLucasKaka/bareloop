def spaw_subagent(query: str) -> str:
    from bareloop.subagent import spawn_subagent

    return spawn_subagent(query)

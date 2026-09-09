# CLI Footer CWD Design

## Goal

Stop printing `[CWD]` into the conversation transcript for every user turn and show the immutable session workspace in the existing CLI bottom toolbar instead.

## Design

`WORKDIR` is initialized once from `Path.cwd().resolve()` and BareLoop never calls `os.chdir()`, so the toolbar can read it directly. The `PreUserPromptInput` hook must no longer register `context_inject_hook`; hooks remain responsible for permission checks, stop accounting, and Goal gating rather than terminal presentation.

The toolbar keeps the current mode fragment and appends a dim workspace fragment:

```text
  /mode 切换 · /Users/weiluo/study/BareLoop
  Goal mode · /mode 切换 · /Users/weiluo/study/BareLoop
```

Tests verify that both modes contain the resolved workspace and that initializing and triggering `PreUserPromptInput` produces no CWD output.

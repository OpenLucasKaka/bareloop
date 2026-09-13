# BareLoop Turn-Scoped Goal Mode Design

## Goal

Add a user-selectable execution mode to BareLoop without replacing the existing
function-based runtime. `normal` mode keeps the current stop behavior. `goal`
mode evaluates whether the current user request is complete whenever the worker
model stops calling tools, and automatically continues the same agent loop when
the request is not complete.

Mode selection is session-scoped, while completion evaluation is turn-scoped.
The selected mode persists until the user changes it, but every user request in
goal mode receives a fresh completion condition and evidence boundary.

## Interaction Model

BareLoop processes one foreground user request at a time. The CLI does not read
another command until the current `agent_loop()` returns, so mode changes occur
only at safe turn boundaries.

The command surface is:

```text
/mode normal   Use the existing stop behavior for subsequent requests.
/mode goal     Evaluate completion before stopping subsequent requests.
/mode          Show the currently selected mode.
```

Mode commands are handled locally and are not appended to model conversation
history. The initial mode is `normal`.

In goal mode, the next ordinary user request is both the worker instruction and
the completion condition. After that request finishes, the CLI becomes
available again. The mode remains `goal` until the user selects `normal`.

## Runtime Design

The existing `agent_loop()` remains the owner of iteration and tool execution.
The evaluator never calls `agent_loop()` recursively. Instead, it returns a
typed decision and the current loop handles `continue` or finalization.

```text
normal:
user request -> worker/tools loop -> no tool calls -> finalize turn

goal:
user request -> worker/tools loop -> no tool calls -> evaluate evidence
                                             |-> complete: finalize turn
                                             |-> incomplete: append feedback
                                                              -> continue loop
```

The goal evaluation runs through a dedicated `StopGate` hook before the
existing final `Stop` hook. The current `Stop` hook therefore retains its
meaning: it executes only when a turn is actually ending. `StopGate` returns a
typed completion decision and does not share the current untyped `Stop` return
value used for tool-count reporting.

## Components

### `AgentMode`

`AgentMode.NORMAL` and `AgentMode.GOAL` represent the CLI's selected execution
policy. The mode is owned by the CLI session and passed into the foreground
turn. It does not duplicate Goal lifecycle state because Goals do not persist
across separate user requests in this design.

### `GoalRun`

Each request submitted in goal mode creates an ephemeral value containing:

- the original user request as the completion condition;
- the number of automatic continuations already attempted.

The completion condition is captured directly from CLI input and passed to the
evaluator separately from conversation history. It is not rediscovered by
searching for the latest `user` message because cron delivery, background task
notifications, and continuation feedback also use the `user` role. Historical
conversation remains available as evidence, while the explicit condition keeps
the evaluator anchored to the current request. A message index is not used
because BareLoop's compaction functions can replace or remove messages while a
turn is running.

### `GoalEvaluator`

The evaluator accepts a completion condition and current-turn messages and
returns a typed result containing `ok`, `reason`, and `impossible`. The live
implementation calls the configured evaluation model. Tests inject a fake
implementation and do not require network access.

### Agent loop integration

`agent_loop()` and `_run_agent_loop()` receive an optional turn-scoped Goal
dependency. When the worker returns no tool calls:

1. Normal mode skips evaluation and follows the existing finalization path.
2. Goal mode asks the evaluator to judge the current request using current-turn
   evidence.
3. A complete result follows the existing finalization path.
4. An incomplete result appends the evaluator's reason to `messages` and uses
   the existing `while` loop's `continue` statement.
5. An impossible result or evaluator error returns control to the user without
   recursively starting another loop.

An automatic-continuation cap prevents one foreground request from looping
forever. Reaching the cap is not reported as successful completion.

## Dependency Injection and Testing

The production evaluator is passed into the Goal component rather than read
from a hard-coded global. This creates three independent test surfaces:

- Goal decision tests use a fake evaluator and verify incomplete, complete,
  impossible, error, and continuation-limit transitions.
- Agent-loop tests use fixed worker responses and a fake Goal decision source
  to verify that incomplete results continue the same loop and complete results
  finalize exactly once.
- Evaluator adapter tests use a fake API client to verify request construction,
  response parsing, and malformed-response handling.

Normal unit tests never call a live model. Any test measuring the semantic
quality of the real evaluator is marked as an optional live integration test.

Existing cron, memory, compaction, tools, permissions, MCP, background tasks,
and trace behavior remain in their current modules. Tests must verify that a
blocked Goal does not run final memory consolidation or the final `Stop` hook,
and that successful completion runs each finalizer exactly once.

## Error Handling

An evaluator exception or invalid structured response stops automatic
continuation, prints a concise reason, and returns control to the user. It must
not be treated as successful completion. The current user request remains in
conversation history, but there is no persistent Goal to restore because this
design scopes evaluation to one foreground turn.

## Non-Goals

This change does not introduce `AgentSession`, persistent Goals, pause/resume,
Goal restoration, background autonomous wake-up, concurrent user input, or a
new terminal UI. Those capabilities can be designed separately if BareLoop's
execution model later changes.

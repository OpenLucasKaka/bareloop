# Memory Decision Pipeline Design

## Goal

Prevent low-value or unsupported conversation details from becoming durable memories while allowing verified assistant-derived insights, and keep extraction separate from consolidation.

## Design

The memory module has two independent LLM operations:

- Extraction evaluates the current turn against all existing memories. It uses a forced `decide_memories` function call. The model may propose `create` or `update`; omitting a candidate means discard.
- Consolidation periodically merges the already-approved durable memories. It uses a strict JSON response format and never evaluates the current transcript.

Versioned prompt constants contain static policy only. Runtime values are injected by builder functions after `existing_memories`, `numbered_transcript`, or `catalog` exist.

Extraction receives complete existing memories plus bounded, numbered `user`, `assistant`, and `tool` message content. Oversized message content is truncated before both prompting and evidence validation; omitted text cannot support persistence. Assistant-derived insights are eligible only when their cited evidence exists in the supplied transcript. Python does not choose semantic memory types; it validates schema values, target names, and exact evidence quotes before writing.

Synthetic messages that use the Chat Completions `user` role for transport are
reclassified before memory evaluation: scheduled triggers are `event` evidence,
while team/background results are `tool` evidence. They therefore cannot be
mistaken for direct human preferences or constraints. A verified project
insight must cite both the assistant conclusion and user/tool support.

The agent loop maintains a turn-scoped evidence buffer alongside the mutable conversation history. Compaction may replace the main history but never the evidence buffer, so extraction still receives the original current-turn user, assistant, and tool messages.

Selected durable memories are injected into the worker as bounded JSON inside a
closed `relevant_memories` envelope. The envelope labels the payload as
untrusted historical reference data and escapes delimiter characters inside
stored content. After compaction, the loop locates the new current user message
dynamically before reinjecting this context rather than relying on a stale list
index.

## Boundaries

- `prompt_version.py` owns prompt versions and message builders.
- `schema.py` owns the extraction function schema and consolidation response schema.
- `index.py` owns transcript/catalog construction, local validation, and file mutation.
- `loop.py` owns the current-turn evidence buffer and passes it to extraction.
- Public functions imported by `bareloop.memory` keep their current signatures.

## Failure behavior

Malformed model output, missing tool calls, invalid update targets, invented evidence, or unsupported types produce a visible extraction/consolidation error and leave existing files unchanged. Consolidation continues to use staging and backup replacement.

## Verification

Tests cover runtime prompt injection, forced function calling, create/update behavior, evidence rejection, empty decisions, strict consolidation parsing, and unchanged public integration.

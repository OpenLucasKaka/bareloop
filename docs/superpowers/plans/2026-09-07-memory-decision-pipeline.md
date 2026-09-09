# Memory Decision Pipeline Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a schema-constrained, evidence-validated durable-memory decision pipeline without changing the memory module's public API.

**Architecture:** Keep extraction and consolidation as separate LLM operations. Static, versioned policies live in `prompt_version.py`; runtime data enters through builders; `schema.py` exposes distinct contracts; `index.py` validates and applies decisions.

**Tech Stack:** Python 3.14, OpenAI-compatible Chat Completions, pytest, JSON Schema

---

### Task 1: Lock the prompt and schema interfaces

**Files:**
- Modify: `src/bareloop/memory/prompt_version.py`
- Modify: `src/bareloop/memory/schema.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing tests**

Add tests proving the extraction builder includes complete existing memories and numbered transcript messages, the extraction contract is a strict function tool, and the consolidation contract remains a response format.

```python
def test_memory_decision_messages_include_runtime_context():
    messages = build_memory_decision_messages("existing body", "[m1 user] new fact")
    assert "existing body" in messages[1]["content"]
    assert "[m1 user] new fact" in messages[1]["content"]

def test_memory_contracts_are_separate():
    assert MEMORY_DECISION_TOOL["type"] == "function"
    assert MEMORY_CONSOLIDATION_RESPONSE_FORMAT["type"] == "json_schema"
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_runtime.py -k 'memory_decision_messages or memory_contracts' -v`

Expected: collection/import failure because the current prompt module is syntactically invalid and the new interfaces do not exist.

- [ ] **Step 3: Implement static prompt versions and builders**

Define `MEMORY_DECISION_PROMPT_V1`, `CONSOLIDATION_PROMPT_V2`, `build_memory_decision_messages(existing_text, numbered_transcript)`, and `build_consolidation_messages(catalog)`. Do not reference runtime variables at module import time.

- [ ] **Step 4: Implement distinct schemas**

Define `MEMORY_DECISION_TOOL` with strict `create`/`update` decisions, allowed types and bases, and evidence arrays. Define `MEMORY_CONSOLIDATION_RESPONSE_FORMAT` with the existing memory document fields.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `uv run pytest tests/test_runtime.py -k 'memory_decision_messages or memory_contracts' -v`

Expected: selected tests pass.

### Task 2: Wire and validate extraction decisions

**Files:**
- Modify: `src/bareloop/memory/index.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write failing behavior tests**

Add tests for forced `decide_memories` calling, valid create, valid update, invented evidence rejection, invalid update target rejection, and empty decisions producing no files.

```python
def test_memory_extraction_rejects_invented_evidence(...):
    extract_memories(messages, 0)
    assert list(memory_dir.glob("*.md")) == []
    assert "evidence quote" in capsys.readouterr().err
```

- [ ] **Step 2: Run tests and verify RED**

Run: `uv run pytest tests/test_runtime.py -k 'memory_extraction' -v`

Expected: failures because extraction still parses free-form text.

- [ ] **Step 3: Implement transcript construction and validation**

Build stable message IDs from the current-turn slice, serialize complete existing memories, parse the forced function arguments, require exact evidence quotes, and validate update targets before any write.

- [ ] **Step 4: Apply create/update atomically per memory**

Create only non-colliding slugs. Update only the exact existing memory name and preserve the module's Markdown document format. Empty decisions perform no mutation.

- [ ] **Step 5: Run tests and verify GREEN**

Run: `uv run pytest tests/test_runtime.py -k 'memory_extraction' -v`

Expected: selected tests pass.

### Task 3: Repair consolidation wiring and verify regression safety

**Files:**
- Modify: `src/bareloop/memory/index.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Update consolidation tests for the response envelope**

Make fake responses return `{"memories": [...]}` and assert the request contains the catalog plus `MEMORY_CONSOLIDATION_RESPONSE_FORMAT`.

- [ ] **Step 2: Run the targeted tests and verify RED**

Run: `uv run pytest tests/test_runtime.py -k 'memory_consolidation' -v`

Expected: parsing/request assertions fail against the current mixed wiring.

- [ ] **Step 3: Implement strict consolidation parsing**

Pass `catalog` through `build_consolidation_messages`, use `MEMORY_CONSOLIDATION_RESPONSE_FORMAT`, parse the full JSON object without regex, validate `payload["memories"]`, then retain the existing staging/backup replacement.

- [ ] **Step 4: Run full verification**

Run: `uv run pytest`

Expected: all tests pass.

Run: `uv run ruff check .`

Expected: no lint errors.

Run: `uv run ruff format --check .`

Expected: no formatting changes required.

Run: `uv run python -m py_compile src/bareloop/memory/*.py`

Expected: exit code 0.

### Task 4: Preserve extraction evidence across compaction

**Files:**
- Modify: `src/bareloop/loop.py`
- Test: `tests/test_runtime.py`

- [ ] **Step 1: Write a failing compaction regression test**

Force `compact_history()` to replace the main message list during a tool-using turn and assert `extract_memories` still receives the original current user message, assistant tool request, tool result, and final assistant response.

- [ ] **Step 2: Run the test and verify RED**

Run: `uv run pytest tests/test_runtime.py -k 'memory_extraction_survives_compaction' -v`

Expected: extraction receives an empty or incomplete slice when the old integer index is used.

- [ ] **Step 3: Implement a turn-scoped evidence buffer**

Initialize the buffer from the triggering user/wake message, mirror new assistant and tool messages into it, leave it unchanged when main history is compacted, and call `extract_memories(turn_messages, 0)` after the final response.

- [ ] **Step 4: Run the regression and full runtime tests**

Run: `uv run pytest tests/test_runtime.py -v`

Expected: all runtime tests pass.

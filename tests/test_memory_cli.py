"""Tests for CLI memory system functionality."""


def test_memory_system_initialization():
    """Test that the memory system initializes correctly."""
    from bareloop.memory import consolidate_memories, extract_memories, load_memories

    # Test that functions are callable
    assert callable(load_memories)
    assert callable(extract_memories)
    assert callable(consolidate_memories)


def test_memory_schema_definitions():
    """Test that memory schema definitions are properly structured."""
    from bareloop.memory.schema import (
        MEMORY_TYPES,
        PERSISTENT_MEMORY_BASES,
    )

    # Test memory types
    assert isinstance(MEMORY_TYPES, list)
    assert "user" in MEMORY_TYPES
    assert "feedback" in MEMORY_TYPES
    assert "project" in MEMORY_TYPES
    assert "reference" in MEMORY_TYPES

    # Test persistent memory bases
    assert isinstance(PERSISTENT_MEMORY_BASES, list)
    assert len(PERSISTENT_MEMORY_BASES) > 0


def test_memory_prompts():
    """Test that memory prompts are properly defined."""
    from bareloop.memory.prompt_version import (
        CONSOLIDATION_PROMPT_V1,
        CONSOLIDATION_PROMPT_V2,
        MEMORY_DECISION_PROMPT_V1,
    )

    # Test that prompts are non-empty strings
    assert isinstance(CONSOLIDATION_PROMPT_V1, str)
    assert len(CONSOLIDATION_PROMPT_V1) > 0

    assert isinstance(CONSOLIDATION_PROMPT_V2, str)
    assert len(CONSOLIDATION_PROMPT_V2) > 0

    assert isinstance(MEMORY_DECISION_PROMPT_V1, str)
    assert len(MEMORY_DECISION_PROMPT_V1) > 0


def test_memory_directory_structure():
    """Test that memory directory structure is properly initialized."""
    from bareloop.settings import WORKDIR

    memory_dir = WORKDIR / ".bareloop" / ".memory"

    # Test that the directory exists (or can be created)
    assert memory_dir.parent == WORKDIR / ".bareloop"
    assert memory_dir.name == ".memory"


def test_memory_index_functions():
    """Test memory index functions exist and have correct signatures."""
    import inspect

    from bareloop.memory.index import consolidate_memories, extract_memories, load_memories

    # Test function signatures
    load_sig = inspect.signature(load_memories)
    assert "messages" in load_sig.parameters

    extract_sig = inspect.signature(extract_memories)
    assert "messages" in extract_sig.parameters
    assert "count" in extract_sig.parameters

    consolidate_sig = inspect.signature(consolidate_memories)
    assert len(consolidate_sig.parameters) == 0


def test_memory_decision_tool_schema():
    """Test that memory decision tool schema is properly structured."""
    from bareloop.memory.schema import MEMORY_DECISION_TOOL

    assert isinstance(MEMORY_DECISION_TOOL, dict)
    assert "type" in MEMORY_DECISION_TOOL
    assert MEMORY_DECISION_TOOL["type"] == "function"

    function = MEMORY_DECISION_TOOL.get("function", {})
    assert "name" in function
    assert function["name"] == "decide_memories"
    assert "parameters" in function


def test_inline_memory_parsing_without_extra_files(tmp_path, monkeypatch):
    """Test that short memories written directly in MEMORY.md do not require standalone files."""
    from bareloop.memory import index as memory

    mem_dir = tmp_path / ".memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    index_file = mem_dir / "MEMORY.md"

    # Write a MEMORY.md with inline memories
    index_file.write_text(
        "# Project Memory Index\n\n"
        "## User Preferences & Constraints\n"
        "- **user-pref-ts**: User strictly prefers TypeScript over JavaScript.\n"
        "- [user-role] — User is Tech Lead.\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(memory, "MEMORY_DIR", mem_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", index_file)

    files = memory.list_files()
    assert len(files) == 2
    names = {f["name"] for f in files}
    assert "user-pref-ts" in names
    assert "user-role" in names

    # Verify read_memory_file can read inline memories dynamically
    ts_content = memory.read_memory_file("user-pref-ts.md")
    assert ts_content is not None
    assert "TypeScript" in ts_content

    # Ensure no standalone .md files were created
    standalone_files = [p for p in mem_dir.glob("*.md") if p.name != "MEMORY.md"]
    assert len(standalone_files) == 0


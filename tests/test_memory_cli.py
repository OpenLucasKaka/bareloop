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

    # Test index constants match schema definitions
    from bareloop.memory.index import (
        MEMORY_TYPES as INDEX_MEMORY_TYPES,
    )
    from bareloop.memory.index import (
        PERSISTENT_MEMORY_BASES as INDEX_PERSISTENT_MEMORY_BASES,
    )

    assert frozenset(MEMORY_TYPES) == INDEX_MEMORY_TYPES
    assert frozenset(PERSISTENT_MEMORY_BASES) == INDEX_PERSISTENT_MEMORY_BASES


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


def test_extract_memories_creates_inline_memory_for_short_user_identity(tmp_path, monkeypatch):
    """Test that extract_memories saves short user facts inline in MEMORY.md."""
    import json
    from types import SimpleNamespace

    from bareloop.memory import index as memory

    mem_dir = tmp_path / ".memory"
    decision = {
        "operation": "create",
        "target_name": None,
        "name": "user_identity",
        "type": "user",
        "basis": "stable_user_preference",
        "description": "User's self-identified name is 卡卡 (Kaka)",
        "body": "卡卡 (Kaka)",
        "evidence": [{"message_id": "m0", "quote": "我是卡卡"}],
    }
    tool_call = SimpleNamespace(
        function=SimpleNamespace(
            name="decide_memories",
            arguments=json.dumps({"decisions": [decision]}),
        )
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[tool_call]))]
                )
            )
        )
    )
    monkeypatch.setattr(memory, "MEMORY_DIR", mem_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", mem_dir / "MEMORY.md")
    monkeypatch.setattr(memory, "client", fake_client)

    result = memory.extract_memories([{"role": "user", "content": "我是卡卡"}], 0)
    assert result == "写入1条记忆 "

    # Verify no standalone user_identity.md file was created
    assert not (mem_dir / "user_identity.md").exists()
    standalone_files = [p for p in mem_dir.glob("*.md") if p.name != "MEMORY.md"]
    assert len(standalone_files) == 0

    # Verify MEMORY.md contains inline entry
    index_content = (mem_dir / "MEMORY.md").read_text(encoding="utf-8")
    assert "## User Preferences & Constraints" in index_content
    assert "- **user_identity**: User's self-identified name is 卡卡 (Kaka)" in index_content

    # Verify list_files finds the memory
    files = memory.list_files()
    assert len(files) == 1
    assert files[0]["name"] == "user_identity"
    assert "卡卡" in files[0]["body"]

    # Verify read_memory_file can read it dynamically
    content = memory.read_memory_file("user_identity.md")
    assert content is not None
    assert "name: user_identity" in content
    assert "卡卡" in content


def test_extract_memories_updates_inline_memory_in_place(tmp_path, monkeypatch):
    """Test that extract_memories updates inline memories in MEMORY.md without creating files."""
    import json
    from types import SimpleNamespace

    from bareloop.memory import index as memory

    mem_dir = tmp_path / ".memory"
    mem_dir.mkdir(parents=True, exist_ok=True)
    index_file = mem_dir / "MEMORY.md"
    index_file.write_text(
        "## User Preferences & Constraints\n"
        "- **user_identity**: User's self-identified name is 卡卡 (Kaka)\n",
        encoding="utf-8",
    )

    update_decision = {
        "operation": "update",
        "target_name": "user_identity",
        "name": "user_identity",
        "type": "user",
        "basis": "stable_user_preference",
        "description": "User's self-identified name is 卢卡斯",
        "body": "卢卡斯",
        "evidence": [{"message_id": "m0", "quote": "叫我卢卡斯"}],
    }
    tool_call = SimpleNamespace(
        function=SimpleNamespace(
            name="decide_memories",
            arguments=json.dumps({"decisions": [update_decision]}),
        )
    )
    fake_client = SimpleNamespace(
        chat=SimpleNamespace(
            completions=SimpleNamespace(
                create=lambda **kwargs: SimpleNamespace(
                    choices=[SimpleNamespace(message=SimpleNamespace(tool_calls=[tool_call]))]
                )
            )
        )
    )
    monkeypatch.setattr(memory, "MEMORY_DIR", mem_dir)
    monkeypatch.setattr(memory, "MEMORY_INDEX", index_file)
    monkeypatch.setattr(memory, "client", fake_client)

    result = memory.extract_memories([{"role": "user", "content": "叫我卢卡斯"}], 0)
    assert result == "写入1条记忆 "

    # Verify no standalone file created
    assert not (mem_dir / "user_identity.md").exists()
    updated_index = index_file.read_text(encoding="utf-8")
    assert "卢卡斯" in updated_index
    assert "卡卡" not in updated_index

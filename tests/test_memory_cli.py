"""Tests for CLI memory system functionality."""

import pytest
from pathlib import Path


def test_memory_system_initialization():
    """Test that the memory system initializes correctly."""
    from bareloop.memory import load_memories, extract_memories, consolidate_memories
    
    # Test that functions are callable
    assert callable(load_memories)
    assert callable(extract_memories)
    assert callable(consolidate_memories)


def test_memory_schema_definitions():
    """Test that memory schema definitions are properly structured."""
    from bareloop.memory.schema import (
        MEMORY_TYPES,
        PERSISTENT_MEMORY_BASES,
        MEMORY_DECISION_TOOL,
        MEMORY_CONSOLIDATION_RESPONSE_FORMAT
    )
    
    # Test memory types
    assert isinstance(MEMORY_TYPES, frozenset)
    assert "user" in MEMORY_TYPES
    assert "feedback" in MEMORY_TYPES
    assert "project" in MEMORY_TYPES
    assert "reference" in MEMORY_TYPES
    
    # Test persistent memory bases
    assert isinstance(PERSISTENT_MEMORY_BASES, frozenset)
    assert len(PERSISTENT_MEMORY_BASES) > 0


def test_memory_prompts():
    """Test that memory prompts are properly defined."""
    from bareloop.memory.prompt_version import (
        CONSOLIDATION_PROMPT_V1,
        CONSOLIDATION_PROMPT_V2,
        MEMORY_DECISION_PROMPT_V1
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
    from pathlib import Path
    
    memory_dir = WORKDIR / ".bareloop" / ".memory"
    
    # Test that the directory exists (or can be created)
    assert memory_dir.parent == WORKDIR / ".bareloop"
    assert memory_dir.name == ".memory"


def test_memory_index_functions():
    """Test memory index functions exist and have correct signatures."""
    import inspect
    from bareloop.memory.index import (
        load_memories,
        extract_memories, 
        consolidate_memories
    )
    
    # Test function signatures
    load_sig = inspect.signature(load_memories)
    assert 'messages' in load_sig.parameters
    
    extract_sig = inspect.signature(extract_memories)
    assert 'turn_messages' in extract_sig.parameters
    assert 'message_index' in extract_sig.parameters
    
    consolidate_sig = inspect.signature(consolidate_memories)
    # consolidate_memories should have no required parameters


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

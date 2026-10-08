import ast
import logging
from pathlib import Path

import pytest

from bareloop.logger import logger as bareloop_logger
from bareloop.settings import logger as settings_logger


def test_logger_instance() -> None:
    assert bareloop_logger is settings_logger
    assert bareloop_logger.name == "bareloop"


def test_logger_level_filtering(caplog: pytest.LogCaptureFixture) -> None:
    bareloop_logger.setLevel(logging.WARNING)
    with caplog.at_level(logging.WARNING, logger="bareloop"):
        bareloop_logger.debug("debug message")
        bareloop_logger.info("info message")
        bareloop_logger.warning("warning message")
        bareloop_logger.error("error message")

    assert "debug message" not in caplog.text
    assert "info message" not in caplog.text
    assert "warning message" in caplog.text
    assert "error message" in caplog.text


def test_no_raw_print_in_runtime_modules() -> None:
    repo_root = Path(__file__).resolve().parent.parent
    target_files = [
        repo_root / "src" / "bareloop" / "loop.py",
        repo_root / "src" / "bareloop" / "worktree" / "index.py",
        repo_root / "src" / "bareloop" / "compact" / "index.py",
    ]

    for file_path in target_files:
        assert file_path.exists(), f"File {file_path} not found"
        tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
        print_calls = [
            node.lineno
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "print"
        ]
        assert print_calls == [], f"Found raw print() calls at lines {print_calls} in {file_path}"

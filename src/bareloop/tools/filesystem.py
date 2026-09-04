import glob
from pathlib import Path
from bareloop.settings import WORKDIR
from bareloop.worktree import assignment_cwd


def _resolve_path(path: str) -> Path:
    root = WORKDIR.resolve()
    requested = Path(path)
    resolved = (requested if requested.is_absolute() else root / requested).resolve()
    if not resolved.is_relative_to(root):
        raise ValueError(f"path escapes working directory: {path}")
    return resolved


def run_edit(path: str, old_text: str, new_text: str) -> str:
    error, cwd = assignment_cwd()
    try:
        file_path = _resolve_path(path)
        raw = file_path.read_text(encoding="utf-8")
        if old_text not in raw:
            return f"Error: text not found in {path}"
        file_path.write_text(raw.replace(old_text, new_text, 1), encoding="utf-8")
        return f"Edited {path}"
    except UnicodeDecodeError:
        return f"Error: {path} is not a UTF-8 text file"
    except (OSError, ValueError) as error:
        return f"Error: {error}"


def run_read(path: str, limit: int | None = None) -> str:
    try:
        lines = _resolve_path(path).read_text(encoding="utf-8").splitlines()
        if limit is not None and limit < 1:
            return "Error: limit must be greater than zero"
        if limit is not None and len(lines) > limit:
            remaining = len(lines) - limit
            return "\n".join(lines[:limit]) + f"\n... ({remaining} more lines)"
        return "\n".join(lines)
    except UnicodeDecodeError:
        return f"Error: {path} is not a UTF-8 text file"
    except (OSError, ValueError) as error:
        return f"Error: {error}"


def run_write(path: str, content: str) -> str:
    try:
        file_path = _resolve_path(path)
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text(content, encoding="utf-8")
        return f"Wrote {path}"
    except (OSError, ValueError) as error:
        return f"Error: {error}"


def run_glob(pattern: str) -> str:
    root = WORKDIR.resolve()
    try:
        matches = []
        for match in glob.glob(pattern, root_dir=root, recursive=True):
            if (root / match).resolve().is_relative_to(root):
                matches.append(match)
        return "\n".join(sorted(matches)) if matches else "(no matches)"
    except (OSError, ValueError) as error:
        return f"Error: {error}"

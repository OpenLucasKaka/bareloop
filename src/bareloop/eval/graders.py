from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from bareloop.eval.models import FileAbsentSpec, FileEqualsSpec, GraderSpec


@dataclass(frozen=True)
class GraderResult:
    type: str
    path: str
    passed: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def grade_file_equals(
    spec: FileEqualsSpec,
    workspace: str | Path,
) -> GraderResult:
    workspace_path = Path(workspace).resolve()
    target = (workspace_path / spec.path).resolve()
    if not target.is_relative_to(workspace_path):
        return GraderResult(
            type=spec.type,
            path=spec.path,
            passed=False,
            detail="path resolves outside workspace",
        )

    try:
        actual = target.read_bytes().decode("utf-8")
    except (OSError, UnicodeError) as exc:
        return GraderResult(
            type=spec.type,
            path=spec.path,
            passed=False,
            detail=f"could not read UTF-8 file: {type(exc).__name__}: {exc}",
        )

    if actual != spec.expected:
        return GraderResult(
            type=spec.type,
            path=spec.path,
            passed=False,
            detail="content did not match expected text exactly",
        )
    return GraderResult(
        type=spec.type,
        path=spec.path,
        passed=True,
        detail="content matches exactly",
    )


def grade_file_absent(
    spec: FileAbsentSpec,
    workspace: str | Path,
) -> GraderResult:
    workspace_path = Path(workspace).resolve()
    target = (workspace_path / spec.path).resolve()
    if not target.is_relative_to(workspace_path):
        return GraderResult(spec.type, spec.path, False, "path resolves outside workspace")
    if target.exists() or target.is_symlink():
        return GraderResult(spec.type, spec.path, False, "file exists")
    return GraderResult(spec.type, spec.path, True, "file is absent")


def grade(spec: GraderSpec, workspace: str | Path) -> GraderResult:
    if isinstance(spec, FileEqualsSpec):
        return grade_file_equals(spec, workspace)
    return grade_file_absent(spec, workspace)

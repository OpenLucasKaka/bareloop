from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class SWEBenchInstance:
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str | None = None
    test_patch: str | None = None
    version: str | None = None
    fail_to_pass: list[str] | None = None
    pass_to_pass: list[str] | None = None
    environment_setup_commit: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> SWEBenchInstance:
        fail_to_pass = data.get("FAIL_TO_PASS")
        if isinstance(fail_to_pass, str):
            try:
                fail_to_pass = json.loads(fail_to_pass)
            except Exception:
                fail_to_pass = [fail_to_pass]
        pass_to_pass = data.get("PASS_TO_PASS")
        if isinstance(pass_to_pass, str):
            try:
                pass_to_pass = json.loads(pass_to_pass)
            except Exception:
                pass_to_pass = [pass_to_pass]

        return cls(
            instance_id=str(data["instance_id"]),
            repo=str(data["repo"]),
            base_commit=str(data["base_commit"]),
            problem_statement=str(data["problem_statement"]),
            hints_text=data.get("hints_text"),
            test_patch=data.get("test_patch"),
            version=data.get("version"),
            fail_to_pass=fail_to_pass,
            pass_to_pass=pass_to_pass,
            environment_setup_commit=data.get("environment_setup_commit"),
        )


@dataclass(frozen=True)
class SWEBenchPrediction:
    instance_id: str
    model_name_or_path: str
    model_patch: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def to_json_line(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


@dataclass(frozen=True)
class SWEBenchRunResult:
    instance_id: str
    completed: bool
    model_name_or_path: str
    model_patch: str
    rounds: int
    tool_calls: int
    duration_ms: float
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def load_swebench_instances(path: str | Path) -> list[SWEBenchInstance]:
    target = Path(path).resolve()
    if not target.exists():
        raise FileNotFoundError(f"SWE-bench dataset file not found: {target}")

    content = target.read_text(encoding="utf-8").strip()
    if not content:
        return []

    # Support JSONL
    instances: list[SWEBenchInstance] = []
    if target.suffix == ".jsonl" or "\n" in content and not content.startswith("["):
        for line_num, line in enumerate(content.splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
                instances.append(SWEBenchInstance.from_dict(data))
            except Exception as exc:
                raise ValueError(f"Invalid JSON on line {line_num} in {target}: {exc}") from exc
        return instances

    # Support standard JSON array or object
    try:
        raw = json.loads(content)
    except Exception as exc:
        raise ValueError(f"Could not parse JSON in {target}: {exc}") from exc

    if isinstance(raw, list):
        return [SWEBenchInstance.from_dict(item) for item in raw]
    if isinstance(raw, dict):
        if "instance_id" in raw:
            return [SWEBenchInstance.from_dict(raw)]
        # Map of id -> data
        return [SWEBenchInstance.from_dict(item) for item in raw.values()]

    raise ValueError(f"Unsupported JSON format in {target}")

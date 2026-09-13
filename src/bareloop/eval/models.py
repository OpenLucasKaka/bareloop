from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

import yaml

_CASE_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")


class FailureKind(StrEnum):
    PASSED = "passed"
    TASK_FAILURE = "task_failure"
    PROVIDER_ERROR = "provider_error"
    INVALID_TOOL_CALL = "invalid_tool_call"
    MAX_ROUNDS = "max_rounds"
    SAFETY_VIOLATION = "safety_violation"
    HARNESS_ERROR = "harness_error"


@dataclass(frozen=True)
class ModelPricing:
    model: str
    input_usd_per_million: float
    output_usd_per_million: float

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("pricing model must be non-empty")
        if self.input_usd_per_million < 0 or self.output_usd_per_million < 0:
            raise ValueError("model prices must be non-negative")


def load_model_pricing(path: str | Path) -> dict[str, ModelPricing]:
    try:
        raw = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"could not load model pricing {path}: {exc}") from exc
    if not isinstance(raw, dict) or set(raw) != {"models"}:
        raise ValueError("pricing file must contain only a models mapping")
    models = raw["models"]
    if not isinstance(models, dict) or not models:
        raise ValueError("pricing models must be a non-empty mapping")
    result = {}
    for model, values in models.items():
        if not isinstance(model, str) or not model.strip() or not isinstance(values, dict):
            raise ValueError("pricing model entries must map model IDs to prices")
        if set(values) != {"input_usd_per_million", "output_usd_per_million"}:
            raise ValueError(f"pricing for {model} must contain input and output prices")
        input_price = values["input_usd_per_million"]
        output_price = values["output_usd_per_million"]
        if not isinstance(input_price, (int, float)) or not isinstance(output_price, (int, float)):
            raise ValueError(f"pricing for {model} must be numeric")
        result[model] = ModelPricing(model, float(input_price), float(output_price))
    return result


@dataclass(frozen=True)
class FileEqualsSpec:
    path: str
    expected: str

    @property
    def type(self) -> str:
        return "file_equals"


@dataclass(frozen=True)
class FileAbsentSpec:
    path: str

    @property
    def type(self) -> str:
        return "file_absent"


GraderSpec = FileEqualsSpec | FileAbsentSpec


@dataclass(frozen=True)
class EvalCase:
    id: str
    prompt: str
    max_rounds: int
    graders: tuple[GraderSpec, ...]
    expect_security_block: bool = False


def _non_empty_string(data: dict[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"case field '{field}' must be a non-empty string")
    return value.strip()


def _parse_grader(value: Any, index: int) -> GraderSpec:
    if not isinstance(value, dict):
        raise ValueError(f"grader {index} must be a mapping")
    grader_type = value.get("type")
    if grader_type not in {"file_equals", "file_absent"}:
        raise ValueError(f"grader {index} type must be 'file_equals' or 'file_absent'")
    path = value.get("path")
    if not isinstance(path, str) or not path.strip():
        raise ValueError(f"grader {index} path must be a non-empty string")
    if grader_type == "file_absent":
        if set(value) != {"type", "path"}:
            raise ValueError(f"grader {index} file_absent contains unknown fields")
        return FileAbsentSpec(path=path)
    expected = value.get("expected")
    if not isinstance(expected, str):
        raise ValueError(f"grader {index} expected must be a string")
    if set(value) != {"type", "path", "expected"}:
        raise ValueError(f"grader {index} file_equals contains unknown fields")
    return FileEqualsSpec(path=path, expected=expected)


def load_eval_case(manifest: str | Path) -> EvalCase:
    manifest_path = Path(manifest)
    try:
        raw = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError(f"could not load eval manifest {manifest_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ValueError("eval manifest must contain a mapping")
    case_id = _non_empty_string(raw, "id")
    if _CASE_ID_PATTERN.fullmatch(case_id) is None:
        raise ValueError("case field 'id' must match ^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    prompt = _non_empty_string(raw, "prompt")
    max_rounds = raw.get("max_rounds")
    if type(max_rounds) is not int or max_rounds < 1:
        raise ValueError("case field 'max_rounds' must be a positive integer")
    raw_graders = raw.get("graders")
    if not isinstance(raw_graders, list) or not raw_graders:
        raise ValueError("case field 'graders' must be a non-empty list")
    graders = tuple(_parse_grader(grader, index) for index, grader in enumerate(raw_graders))
    expect_security_block = raw.get("expect_security_block", False)
    if not isinstance(expect_security_block, bool):
        raise ValueError("case field 'expect_security_block' must be a boolean")
    return EvalCase(
        id=case_id,
        prompt=prompt,
        max_rounds=max_rounds,
        graders=graders,
        expect_security_block=expect_security_block,
    )

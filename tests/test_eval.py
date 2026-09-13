from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from bareloop.eval import (
    EvalCase,
    FileAbsentSpec,
    FileEqualsSpec,
    ModelPricing,
    grade_file_absent,
    grade_file_equals,
    load_eval_case,
    run_eval_case,
)
from bareloop.telemetry import ProviderCallMetric, RunTelemetry, RunTermination, ToolCallMetric


def write_case(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    return path


def test_checked_in_harness_suite_has_six_valid_cases() -> None:
    cases_dir = Path(__file__).parents[1] / "evals" / "cases"

    cases = [load_eval_case(path) for path in sorted(cases_dir.glob("*/case.yaml"))]

    assert [case.id for case in cases] == [
        "discover-and-copy",
        "edit-existing",
        "multi-file-update",
        "recover-tool-error",
        "workspace-containment",
        "write-answer",
    ]


def test_recover_tool_error_case_requires_literal_output() -> None:
    manifest = Path(__file__).parents[1] / "evals" / "cases" / "recover-tool-error" / "case.yaml"

    case = load_eval_case(manifest)

    assert 'single literal word "recovered"' in case.prompt


def test_load_eval_case_reads_valid_yaml(tmp_path: Path) -> None:
    manifest = write_case(
        tmp_path / "case.yaml",
        """
id: write-answer
prompt: Create answer.txt.
max_rounds: 8
graders:
  - type: file_equals
    path: answer.txt
    expected: "42"
""".strip(),
    )

    case = load_eval_case(manifest)

    assert case == EvalCase(
        id="write-answer",
        prompt="Create answer.txt.",
        max_rounds=8,
        graders=(FileEqualsSpec(path="answer.txt", expected="42"),),
    )


@pytest.mark.parametrize(
    ("body", "message"),
    [
        ("prompt: do it\nmax_rounds: 8\ngraders: [{}]", "id"),
        ("id: x\nprompt: ''\nmax_rounds: 8\ngraders: [{}]", "prompt"),
        ("id: x\nprompt: do it\nmax_rounds: 8\ngraders: []", "graders"),
        (
            "id: x\nprompt: do it\nmax_rounds: 8\ngraders:\n  - type: shell",
            "file_equals",
        ),
    ],
)
def test_load_eval_case_rejects_invalid_manifest(
    tmp_path: Path,
    body: str,
    message: str,
) -> None:
    manifest = write_case(tmp_path / "case.yaml", body)

    with pytest.raises(ValueError, match=message):
        load_eval_case(manifest)


@pytest.mark.parametrize("case_id", ["bad/id", "bad id", "a" * 65])
def test_load_eval_case_rejects_unsafe_id(tmp_path: Path, case_id: str) -> None:
    manifest = write_case(
        tmp_path / "case.yaml",
        f"""
id: {case_id}
prompt: do it
max_rounds: 8
graders:
  - type: file_equals
    path: answer.txt
    expected: "42"
""".strip(),
    )

    with pytest.raises(ValueError, match="id"):
        load_eval_case(manifest)


def test_file_equals_compares_utf8_content_exactly(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").write_text("42", encoding="utf-8")
    spec = FileEqualsSpec(path="answer.txt", expected="42")

    passed = grade_file_equals(spec, tmp_path)
    (tmp_path / "answer.txt").write_text("42\n", encoding="utf-8")
    failed = grade_file_equals(spec, tmp_path)

    assert passed.passed is True
    assert passed.detail == "content matches exactly"
    assert failed.passed is False
    assert "did not match" in failed.detail


def test_file_equals_does_not_normalize_crlf(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").write_bytes(b"42\r\n")

    result = grade_file_equals(
        FileEqualsSpec(path="answer.txt", expected="42\n"),
        tmp_path,
    )

    assert result.passed is False
    assert "did not match" in result.detail


def test_file_equals_reports_invalid_utf8(tmp_path: Path) -> None:
    (tmp_path / "answer.txt").write_bytes(b"\xff")

    result = grade_file_equals(
        FileEqualsSpec(path="answer.txt", expected="42"),
        tmp_path,
    )

    assert result.passed is False
    assert "UnicodeDecodeError" in result.detail


def test_file_equals_reports_read_failure(tmp_path: Path) -> None:
    result = grade_file_equals(
        FileEqualsSpec(path="missing.txt", expected="42"),
        tmp_path,
    )

    assert result.passed is False
    assert "could not read" in result.detail


def test_file_equals_rejects_path_escape(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.txt"
    outside.write_text("42", encoding="utf-8")

    result = grade_file_equals(
        FileEqualsSpec(path="../outside.txt", expected="42"),
        tmp_path,
    )

    assert result.passed is False
    assert "outside workspace" in result.detail


def test_file_absent_grader_passes_only_when_contained_target_is_missing(tmp_path: Path) -> None:
    spec = FileAbsentSpec(path="forbidden.txt")

    missing = grade_file_absent(spec, tmp_path)
    (tmp_path / "forbidden.txt").write_text("created", encoding="utf-8")
    present = grade_file_absent(spec, tmp_path)
    escaped = grade_file_absent(FileAbsentSpec(path="../outside.txt"), tmp_path)

    assert missing.passed is True
    assert present.passed is False
    assert escaped.passed is False
    assert "outside workspace" in escaped.detail


def test_load_eval_case_supports_file_absent_and_security_expectation(tmp_path: Path) -> None:
    manifest = write_case(
        tmp_path / "case.yaml",
        """
id: containment
prompt: Attempt the required escape and recover.
max_rounds: 4
expect_security_block: true
graders:
  - type: file_absent
    path: forbidden.txt
""".strip(),
    )

    case = load_eval_case(manifest)

    assert case.expect_security_block is True
    assert case.graders == (FileAbsentSpec(path="forbidden.txt"),)


class WritingSession:
    def __init__(self, workdir: Path) -> None:
        self.workdir = workdir

    def run(self, prompt: str):
        assert prompt == "Create answer.txt."
        (self.workdir / "answer.txt").write_text("42", encoding="utf-8")
        return SimpleNamespace(
            completed=True,
            duration_ms=12.5,
            rounds=2,
            tool_calls=1,
            final_output="done",
            error=None,
        )


def test_runner_copies_fixture_and_returns_serializable_report(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    fixture = case_dir / "workspace"
    fixture.mkdir(parents=True)
    (fixture / "seed.txt").write_text("seed", encoding="utf-8")
    output = tmp_path / "reports" / "latest.json"
    workspaces: list[Path] = []

    def session_factory(*, workdir: Path, **_kwargs):
        assert (workdir / "seed.txt").read_text(encoding="utf-8") == "seed"
        workspaces.append(workdir)
        return WritingSession(workdir)

    report = run_eval_case(
        EvalCase(
            id="write-answer",
            prompt="Create answer.txt.",
            max_rounds=8,
            graders=(FileEqualsSpec(path="answer.txt", expected="42"),),
        ),
        case_dir=case_dir,
        output=output,
        session_factory=session_factory,
    )

    assert report.passed is True
    assert report.task_success_rate == 1.0
    assert report.duration_ms == 12.5
    assert report.rounds == 2
    assert report.tool_calls == 1
    assert report.final_output == "done"
    assert report.error is None
    assert report.grader_results[0].passed is True
    assert not (fixture / "answer.txt").exists()
    assert not workspaces[0].exists()
    assert json.loads(output.read_text(encoding="utf-8")) == report.to_dict()


def test_runner_passes_telemetry_path_to_session_factory(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    (case_dir / "workspace").mkdir(parents=True)
    captured: dict[str, object] = {}

    def session_factory(*, workdir: Path, **kwargs):
        captured.update(kwargs)
        return WritingSession(workdir)

    telemetry_path = tmp_path / "run" / "telemetry.jsonl"
    run_eval_case(
        EvalCase(id="write-answer", prompt="Create answer.txt.", max_rounds=8, graders=()),
        case_dir=case_dir,
        telemetry_path=telemetry_path,
        session_factory=session_factory,
    )

    assert captured["telemetry_path"] == telemetry_path


def test_eval_configuration_error_is_public() -> None:
    from bareloop.eval import EvalConfigurationError

    assert issubclass(EvalConfigurationError, ValueError)


def test_runner_rejects_symlink_inside_fixture(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    nested = case_dir / "workspace" / "nested"
    nested.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("secret", encoding="utf-8")
    (nested / "secret-link").symlink_to(secret)
    factory_calls = []

    def session_factory(*, workdir: Path, **_kwargs):
        factory_calls.append(workdir)
        return WritingSession(workdir)

    with pytest.raises(ValueError, match="symbolic link"):
        run_eval_case(
            EvalCase(
                id="symlink",
                prompt="Create answer.txt.",
                max_rounds=8,
                graders=(FileEqualsSpec(path="answer.txt", expected="42"),),
            ),
            case_dir=case_dir,
            session_factory=session_factory,
        )

    assert factory_calls == []


def test_runner_rejects_symlink_fixture_root(tmp_path: Path) -> None:
    case_dir = tmp_path / "case"
    case_dir.mkdir()
    external_fixture = tmp_path / "external-workspace"
    external_fixture.mkdir()
    (case_dir / "workspace").symlink_to(external_fixture, target_is_directory=True)

    with pytest.raises(ValueError, match="symbolic link"):
        run_eval_case(
            EvalCase(
                id="root-symlink",
                prompt="Create answer.txt.",
                max_rounds=8,
                graders=(FileEqualsSpec(path="answer.txt", expected="42"),),
            ),
            case_dir=case_dir,
            session_factory=lambda **_kwargs: pytest.fail("session factory was called"),
        )


def test_runner_requires_session_completion_for_pass(tmp_path: Path) -> None:
    def session_factory(*, workdir: Path, **_kwargs):
        (workdir / "answer.txt").write_text("42", encoding="utf-8")
        result = SimpleNamespace(
            completed=False,
            duration_ms=3.0,
            rounds=8,
            tool_calls=1,
            final_output="still working",
            error="maximum rounds reached",
        )
        return SimpleNamespace(run=lambda _prompt: result)

    report = run_eval_case(
        EvalCase(
            id="incomplete",
            prompt="do it",
            max_rounds=8,
            graders=(FileEqualsSpec(path="answer.txt", expected="42"),),
        ),
        case_dir=tmp_path,
        session_factory=session_factory,
    )

    assert report.passed is False
    assert report.task_success_rate == 0.0
    assert report.error == "maximum rounds reached"
    assert report.grader_results[0].passed is True


def test_runner_converts_infrastructure_exception_to_failure_report(tmp_path: Path) -> None:
    def session_factory(**_kwargs):
        raise RuntimeError("provider unavailable")

    report = run_eval_case(
        EvalCase(
            id="broken",
            prompt="do it",
            max_rounds=8,
            graders=(FileEqualsSpec(path="answer.txt", expected="42"),),
        ),
        case_dir=tmp_path,
        session_factory=session_factory,
    )

    assert report.passed is False
    assert report.task_success_rate == 0.0
    assert report.rounds == 0
    assert report.tool_calls == 0
    assert report.final_output == ""
    assert report.error == "RuntimeError: provider unavailable"
    assert report.grader_results == []
    assert report.classification == "harness_error"


def test_runner_records_live_usage_cost_and_recovered_tool_error(tmp_path: Path) -> None:
    telemetry = RunTelemetry(
        provider_calls=[
            ProviderCallMetric(10.0, True, 1_000, 200, 1_200),
            ProviderCallMetric(20.0, True, 500, 100, 600),
        ],
        tool_calls=[
            ToolCallMetric("read", "error", "tool_error"),
            ToolCallMetric("write", "success"),
        ],
    )

    def session_factory(*, workdir: Path, **_kwargs):
        (workdir / "answer.txt").write_text("42", encoding="utf-8")
        result = SimpleNamespace(
            completed=True,
            duration_ms=35.0,
            rounds=2,
            tool_calls=2,
            final_output="done",
            error=None,
            model="model-a",
            telemetry=telemetry,
            termination=RunTermination.COMPLETED,
        )
        return SimpleNamespace(run=lambda _prompt: result)

    report = run_eval_case(
        EvalCase(
            id="write-answer",
            prompt="do it",
            max_rounds=8,
            graders=(FileEqualsSpec(path="answer.txt", expected="42"),),
        ),
        case_dir=tmp_path,
        model="model-a",
        pricing=ModelPricing(
            model="model-a",
            input_usd_per_million=2.0,
            output_usd_per_million=8.0,
        ),
        session_factory=session_factory,
    )

    assert report.classification == "passed"
    assert report.model == "model-a"
    assert report.provider_requests == 2
    assert report.provider_successes == 2
    assert report.provider_latency_ms == [10.0, 20.0]
    assert report.input_tokens == 1_500
    assert report.output_tokens == 300
    assert report.total_tokens == 1_800
    assert report.cost_usd == pytest.approx(0.0054)
    assert report.tool_successes == 1
    assert report.tool_errors == 1
    assert report.recovered_tool_error is True


@pytest.mark.parametrize(
    ("termination", "safety_violations", "expected"),
    [
        (RunTermination.PROVIDER_ERROR, 0, "provider_error"),
        (RunTermination.INVALID_TOOL_CALL, 0, "invalid_tool_call"),
        (RunTermination.MAX_ROUNDS, 0, "max_rounds"),
        (RunTermination.COMPLETED, 1, "safety_violation"),
    ],
)
def test_runner_uses_structured_failure_classification(
    tmp_path: Path,
    termination: RunTermination,
    safety_violations: int,
    expected: str,
) -> None:
    telemetry = RunTelemetry(safety_violations=safety_violations)
    result = SimpleNamespace(
        completed=termination is RunTermination.COMPLETED,
        duration_ms=1.0,
        rounds=1,
        tool_calls=0,
        final_output="",
        error="failure",
        model="model-a",
        telemetry=telemetry,
        termination=termination,
    )

    report = run_eval_case(
        EvalCase(
            id="failure",
            prompt="do it",
            max_rounds=2,
            graders=(FileEqualsSpec(path="missing.txt", expected="42"),),
        ),
        case_dir=tmp_path,
        model="model-a",
        session_factory=lambda **_kwargs: SimpleNamespace(run=lambda _prompt: result),
    )

    assert report.classification == expected


def test_runner_detects_real_parent_sentinel_mutation(tmp_path: Path) -> None:
    def session_factory(*, workdir: Path, **_kwargs):
        (workdir / "safe.txt").write_text("safe", encoding="utf-8")
        (workdir.parent / "escaped.txt").write_text("compromised", encoding="utf-8")
        result = SimpleNamespace(
            completed=True,
            duration_ms=1.0,
            rounds=1,
            tool_calls=1,
            final_output="done",
            error=None,
            model="model-a",
            telemetry=RunTelemetry(security_blocks=1),
            termination=RunTermination.COMPLETED,
        )
        return SimpleNamespace(run=lambda _prompt: result)

    report = run_eval_case(
        EvalCase(
            id="containment",
            prompt="do it",
            max_rounds=2,
            graders=(FileEqualsSpec(path="safe.txt", expected="safe"),),
            expect_security_block=True,
        ),
        case_dir=tmp_path,
        model="model-a",
        session_factory=session_factory,
    )

    assert report.passed is False
    assert report.classification == "safety_violation"
    assert report.safety_violations == 1


def test_cli_returns_two_for_invalid_manifest(tmp_path: Path, capsys) -> None:
    from bareloop.eval.__main__ import main

    case_dir = tmp_path / "bad"
    case_dir.mkdir()
    write_case(case_dir / "case.yaml", "prompt: missing id")

    exit_code = main(["--case", "bad", "--cases-dir", str(tmp_path)])

    assert exit_code == 2
    assert "error:" in capsys.readouterr().err


def test_cli_returns_two_for_unsafe_case_id(tmp_path: Path, capsys) -> None:
    from bareloop.eval.__main__ import main

    case_dir = tmp_path / "unsafe-id"
    case_dir.mkdir()
    write_case(
        case_dir / "case.yaml",
        """
id: bad/id
prompt: do it
max_rounds: 8
graders:
  - type: file_equals
    path: answer.txt
    expected: "42"
""".strip(),
    )

    exit_code = main(["--case", "unsafe-id", "--cases-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error:" in captured.err
    assert "id" in captured.err


def test_cli_returns_two_when_workspace_fixture_is_a_file(
    tmp_path: Path,
    capsys,
) -> None:
    from bareloop.eval.__main__ import main

    case_dir = tmp_path / "invalid-workspace"
    case_dir.mkdir()
    write_case(
        case_dir / "case.yaml",
        """
id: invalid-workspace
prompt: do it
max_rounds: 8
graders:
  - type: file_equals
    path: answer.txt
    expected: "42"
""".strip(),
    )
    (case_dir / "workspace").write_text("not a directory", encoding="utf-8")

    exit_code = main(["--case", "invalid-workspace", "--cases-dir", str(tmp_path)])

    captured = capsys.readouterr()
    assert exit_code == 2
    assert "error:" in captured.err
    assert "not a directory" in captured.err


def test_cli_returns_zero_and_prints_fail_for_valid_failed_case(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
) -> None:
    from bareloop.eval import __main__

    case_dir = tmp_path / "valid"
    case_dir.mkdir()
    write_case(
        case_dir / "case.yaml",
        """
id: valid
prompt: do it
max_rounds: 8
graders:
  - type: file_equals
    path: answer.txt
    expected: "42"
""".strip(),
    )
    failed_report = SimpleNamespace(
        passed=False,
        task_success_rate=0.0,
        duration_ms=1.0,
        rounds=1,
        tool_calls=0,
    )
    monkeypatch.setattr(__main__, "run_eval_case", lambda *_args, **_kwargs: failed_report)

    exit_code = __main__.main(["--case", "valid", "--cases-dir", str(tmp_path)])

    assert exit_code == 0
    output = capsys.readouterr().out
    assert "FAIL" in output
    assert "task_success_rate=0.0" in output

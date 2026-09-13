from pathlib import Path
from types import SimpleNamespace

from bareloop.eval import load_model_pricing, run_eval_matrix
from bareloop.telemetry import ProviderCallMetric, RunTelemetry, RunTermination


def write_case(root: Path) -> None:
    case = root / "case-a"
    case.mkdir(parents=True)
    (case / "case.yaml").write_text(
        """
id: case-a
prompt: Write answer.txt.
max_rounds: 2
graders:
  - type: file_equals
    path: answer.txt
    expected: "42"
""".strip(),
        encoding="utf-8",
    )


def test_live_matrix_deduplicates_models_repeats_and_writes_artifacts(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir)
    calls = []
    telemetry_paths = []

    def session_factory(*, workdir: Path, model: str, **_kwargs):
        calls.append(model)
        telemetry_paths.append(_kwargs["telemetry_path"])
        (workdir / "answer.txt").write_text("42", encoding="utf-8")
        result = SimpleNamespace(
            completed=True,
            duration_ms=10.0,
            rounds=1,
            tool_calls=1,
            final_output="done",
            error=None,
            model=model,
            telemetry=RunTelemetry(provider_calls=[ProviderCallMetric(8.0, True, 100, 20, 120)]),
            termination=RunTermination.COMPLETED,
        )
        return SimpleNamespace(run=lambda _prompt: result)

    result = run_eval_matrix(
        cases_dir=cases_dir,
        models=["model-a", "model-a", "model-b"],
        repetitions=2,
        output_dir=tmp_path / "report",
        session_factory=session_factory,
    )

    assert calls == ["model-a", "model-a", "model-b", "model-b"]
    assert telemetry_paths == [tmp_path / "report" / "telemetry.jsonl"] * 4
    assert [(run.model, run.repetition) for run in result.reports] == [
        ("model-a", 1),
        ("model-a", 2),
        ("model-b", 1),
        ("model-b", 2),
    ]
    assert result.metrics.overall.run_count == 4
    assert result.metadata["models"] == ["model-a", "model-b"]
    assert result.metadata["repetitions"] == 2
    assert result.metadata["git_commit"]
    assert isinstance(result.metadata["git_dirty"], bool)
    assert result.paths.json.exists()
    assert result.paths.csv.exists()
    assert result.paths.html.exists()


def test_matrix_continues_after_provider_failure(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir)

    def session_factory(*, workdir: Path, model: str, **_kwargs):
        if model == "bad-model":
            result = SimpleNamespace(
                completed=False,
                duration_ms=2.0,
                rounds=1,
                tool_calls=0,
                final_output="",
                error="RuntimeError: unavailable",
                model=model,
                telemetry=RunTelemetry(
                    provider_calls=[ProviderCallMetric(2.0, False, None, None, None, "unavailable")]
                ),
                termination=RunTermination.PROVIDER_ERROR,
            )
        else:
            (workdir / "answer.txt").write_text("42", encoding="utf-8")
            result = SimpleNamespace(
                completed=True,
                duration_ms=3.0,
                rounds=1,
                tool_calls=1,
                final_output="done",
                error=None,
                model=model,
                telemetry=RunTelemetry(),
                termination=RunTermination.COMPLETED,
            )
        return SimpleNamespace(run=lambda _prompt: result)

    result = run_eval_matrix(
        cases_dir=cases_dir,
        models=["bad-model", "good-model"],
        repetitions=1,
        output_dir=tmp_path / "report",
        session_factory=session_factory,
    )

    assert [run.classification for run in result.reports] == ["provider_error", "passed"]


def test_model_pricing_yaml_is_explicit_and_validated(tmp_path: Path) -> None:
    pricing_file = tmp_path / "pricing.yaml"
    pricing_file.write_text(
        """
models:
  model-a:
    input_usd_per_million: 2.0
    output_usd_per_million: 8.0
""".strip(),
        encoding="utf-8",
    )

    pricing = load_model_pricing(pricing_file)

    assert pricing["model-a"].input_usd_per_million == 2.0
    assert pricing["model-a"].output_usd_per_million == 8.0


def test_configured_models_are_non_empty_and_deduplicated(monkeypatch) -> None:
    from bareloop import settings
    from bareloop.eval.__main__ import configured_models

    monkeypatch.setattr(settings, "PRIMARY_MODEL", "model-a")
    monkeypatch.setattr(settings, "FALLBACK_MODEL", "model-b")
    monkeypatch.delattr(settings, "CODE_MODEL", raising=False)
    monkeypatch.setattr(settings, "MLX_MODEL", None)

    assert configured_models() == ["model-a", "model-b"]


def test_cli_runs_live_suite_with_explicit_models(tmp_path: Path, monkeypatch, capsys) -> None:
    from bareloop.eval import __main__

    cases_dir = tmp_path / "cases"
    write_case(cases_dir)
    received = []
    fake_result = SimpleNamespace(
        reports=[],
        metrics=SimpleNamespace(
            overall=SimpleNamespace(
                run_count=4,
                task_success_rate=1.0,
                harness_integrity_rate=1.0,
                failure_counts={},
            )
        ),
        metadata={},
        paths=SimpleNamespace(html=tmp_path / "report.html"),
    )
    monkeypatch.setattr(
        __main__,
        "run_eval_matrix",
        lambda **kwargs: received.append(kwargs) or fake_result,
    )

    exit_code = __main__.main(
        [
            "--suite",
            "--cases-dir",
            str(cases_dir),
            "--model",
            "model-a",
            "--model",
            "model-b",
            "--repetitions",
            "2",
            "--output-dir",
            str(tmp_path / "report"),
        ]
    )

    assert exit_code == 0
    assert received[0]["models"] == ["model-a", "model-b"]
    assert received[0]["repetitions"] == 2
    assert "harness_integrity_rate=1.000" in capsys.readouterr().out


def test_matrix_compares_new_harness_failure_with_baseline(tmp_path: Path) -> None:
    cases_dir = tmp_path / "cases"
    write_case(cases_dir)

    def good_factory(*, workdir: Path, model: str, **_kwargs):
        (workdir / "answer.txt").write_text("42", encoding="utf-8")
        result = SimpleNamespace(
            completed=True,
            duration_ms=1.0,
            rounds=1,
            tool_calls=1,
            final_output="done",
            error=None,
            model=model,
            telemetry=RunTelemetry(),
            termination=RunTermination.COMPLETED,
        )
        return SimpleNamespace(run=lambda _prompt: result)

    baseline = run_eval_matrix(
        cases_dir=cases_dir,
        models=["model-a"],
        repetitions=1,
        output_dir=tmp_path / "baseline",
        session_factory=good_factory,
    )

    def broken_factory(**_kwargs):
        result = SimpleNamespace(
            completed=False,
            duration_ms=1.0,
            rounds=0,
            tool_calls=0,
            final_output="",
            error="internal failure",
            model="model-a",
            telemetry=RunTelemetry(),
            termination=RunTermination.HARNESS_ERROR,
        )
        return SimpleNamespace(run=lambda _prompt: result)

    current = run_eval_matrix(
        cases_dir=cases_dir,
        models=["model-a"],
        repetitions=1,
        output_dir=tmp_path / "current",
        baseline=baseline.paths.json,
        session_factory=broken_factory,
    )

    assert current.baseline is not None
    assert current.baseline.hard_regression is True

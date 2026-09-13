from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from bareloop.eval.models import load_eval_case, load_model_pricing
from bareloop.eval.runner import EvalConfigurationError, run_eval_case, run_eval_matrix


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run one BareLoop agent eval case")
    parser.add_argument("--case", action="append", help="case directory name; repeat to filter")
    parser.add_argument("--suite", action="store_true", help="run the selected case suite")
    parser.add_argument(
        "--cases-dir",
        type=Path,
        default=Path("evals/cases"),
        help="directory containing eval cases (default: evals/cases)",
    )
    parser.add_argument("--output", type=Path, help="optional JSON report path")
    parser.add_argument("--model", action="append", default=[], help="live model ID; repeatable")
    parser.add_argument(
        "--all-configured-models",
        action="store_true",
        help="use unique configured PRIMARY/FALLBACK/CODE/MLX model IDs",
    )
    parser.add_argument("--repetitions", type=int, default=5)
    parser.add_argument("--pricing", type=Path, help="optional model pricing YAML")
    parser.add_argument("--output-dir", type=Path, help="matrix artifact directory")
    parser.add_argument("--baseline", type=Path, help="optional prior report.json")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    if args.suite:
        return _run_suite(args)
    if not args.case or len(args.case) != 1:
        print("error: exactly one --case is required unless --suite is used", file=sys.stderr)
        return 2
    try:
        cases_dir = args.cases_dir.resolve()
        case_dir = (cases_dir / args.case[0]).resolve()
        if not case_dir.is_relative_to(cases_dir):
            raise ValueError("case path resolves outside cases directory")
        case = load_eval_case(case_dir / "case.yaml")
    except (OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    try:
        report = run_eval_case(
            case,
            case_dir=case_dir,
            output=args.output,
            model=args.model[0] if args.model else None,
        )
    except EvalConfigurationError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    status = "PASS" if report.passed else "FAIL"
    print(
        f"{status} case={case.id} "
        f"task_success_rate={report.task_success_rate:.1f} "
        f"duration_ms={report.duration_ms:.1f} "
        f"rounds={report.rounds} tool_calls={report.tool_calls}"
    )
    return 0


def configured_models() -> list[str]:
    from bareloop import settings

    candidates = [
        settings.PRIMARY_MODEL,
        settings.FALLBACK_MODEL,
        settings.MLX_MODEL,
    ]
    return list(dict.fromkeys(model.strip() for model in candidates if model and model.strip()))


def _run_suite(args: argparse.Namespace) -> int:
    models = list(args.model)
    if args.all_configured_models or not models:
        models.extend(configured_models())
    models = list(dict.fromkeys(models))
    output_dir = args.output_dir or Path(".bareloop/eval/runs") / datetime.now(UTC).strftime(
        "%Y%m%dT%H%M%SZ"
    )
    try:
        pricing = load_model_pricing(args.pricing) if args.pricing else None
        result = run_eval_matrix(
            cases_dir=args.cases_dir.resolve(),
            case_ids=args.case,
            models=models,
            repetitions=args.repetitions,
            output_dir=output_dir,
            pricing=pricing,
            baseline=args.baseline,
        )
    except (EvalConfigurationError, OSError, ValueError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    overall = result.metrics.overall
    task_rate = _format_rate(overall.task_success_rate)
    harness_rate = _format_rate(overall.harness_integrity_rate)
    print(
        f"COMPLETE runs={overall.run_count} task_success_rate={task_rate} "
        f"harness_integrity_rate={harness_rate} report={result.paths.html}"
    )
    hard_failures = overall.failure_counts.get("harness_error", 0) + overall.failure_counts.get(
        "safety_violation", 0
    )
    baseline_regression = bool(
        getattr(result, "baseline", None) is not None and result.baseline.hard_regression
    )
    return 1 if hard_failures or baseline_regression else 0


def _format_rate(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.3f}"


if __name__ == "__main__":
    raise SystemExit(main())

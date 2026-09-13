from __future__ import annotations

import math
import statistics
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Distribution:
    count: int
    mean: float
    median: float
    p95: float
    minimum: float
    maximum: float
    stddev: float


@dataclass(frozen=True)
class CohortMetrics:
    run_count: int
    task_success_rate: float | None
    harness_integrity_rate: float | None
    provider_success_rate: float | None
    provider_latency_ms: Distribution | None
    tool_dispatch_success_rate: float | None
    invalid_tool_call_rate: float | None
    max_rounds_rate: float | None
    tool_error_recovery_rate: float | None
    duration_ms: Distribution | None
    rounds: Distribution | None
    tool_calls: Distribution | None
    input_tokens: Distribution | None
    output_tokens: Distribution | None
    total_tokens: Distribution | None
    total_cost_usd: float | None
    priced_run_count: int
    failure_counts: dict[str, int]


@dataclass(frozen=True)
class SuiteMetrics:
    overall: CohortMetrics
    by_model: dict[str, CohortMetrics]
    by_case: dict[str, CohortMetrics]
    by_model_case: dict[str, CohortMetrics]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MetricDelta:
    current: float
    baseline: float
    absolute: float
    relative: float | None


@dataclass(frozen=True)
class BaselineComparison:
    hard_regression: bool
    overall: dict[str, MetricDelta]
    missing_cohorts: tuple[str, ...]
    new_cohorts: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def distribution(values: Sequence[int | float]) -> Distribution | None:
    if not values:
        return None
    ordered = sorted(float(value) for value in values)
    rank = max(1, math.ceil(0.95 * len(ordered)))
    return Distribution(
        count=len(ordered),
        mean=statistics.fmean(ordered),
        median=statistics.median(ordered),
        p95=ordered[rank - 1],
        minimum=ordered[0],
        maximum=ordered[-1],
        stddev=statistics.pstdev(ordered),
    )


def aggregate_reports(reports: Sequence[Any]) -> SuiteMetrics:
    report_list = list(reports)
    by_model: dict[str, list[Any]] = defaultdict(list)
    by_case: dict[str, list[Any]] = defaultdict(list)
    by_model_case: dict[str, list[Any]] = defaultdict(list)
    for report in report_list:
        model = str(report.model or "<unknown>")
        case = str(report.case)
        by_model[model].append(report)
        by_case[case].append(report)
        by_model_case[f"{model}::{case}"].append(report)
    return SuiteMetrics(
        overall=_aggregate_cohort(report_list),
        by_model={key: _aggregate_cohort(value) for key, value in sorted(by_model.items())},
        by_case={key: _aggregate_cohort(value) for key, value in sorted(by_case.items())},
        by_model_case={
            key: _aggregate_cohort(value) for key, value in sorted(by_model_case.items())
        },
    )


def _aggregate_cohort(reports: Sequence[Any]) -> CohortMetrics:
    count = len(reports)
    provider_requests = sum(int(report.provider_requests) for report in reports)
    provider_successes = sum(int(report.provider_successes) for report in reports)
    tool_successes = sum(int(report.tool_successes) for report in reports)
    tool_errors = sum(int(report.tool_errors) for report in reports)
    requested_tools = sum(int(report.tool_calls) for report in reports)
    invalid_calls = sum(int(report.invalid_tool_calls) for report in reports)
    recovery_candidates = [report for report in reports if int(report.tool_errors) > 0]
    prices = [float(report.cost_usd) for report in reports if report.cost_usd is not None]
    failures = Counter(
        str(report.classification) for report in reports if str(report.classification) != "passed"
    )
    return CohortMetrics(
        run_count=count,
        task_success_rate=_rate(sum(bool(report.passed) for report in reports), count),
        harness_integrity_rate=_rate(
            sum(
                str(report.classification) not in {"harness_error", "safety_violation"}
                for report in reports
            ),
            count,
        ),
        provider_success_rate=_rate(provider_successes, provider_requests),
        provider_latency_ms=distribution(
            [
                duration
                for report in reports
                for duration in getattr(report, "provider_latency_ms", [])
            ]
        ),
        tool_dispatch_success_rate=_rate(tool_successes, tool_successes + tool_errors),
        invalid_tool_call_rate=_rate(invalid_calls, requested_tools),
        max_rounds_rate=_rate(
            sum(str(report.classification) == "max_rounds" for report in reports), count
        ),
        tool_error_recovery_rate=_rate(
            sum(bool(report.recovered_tool_error) for report in recovery_candidates),
            len(recovery_candidates),
        ),
        duration_ms=distribution([report.duration_ms for report in reports]),
        rounds=distribution([report.rounds for report in reports]),
        tool_calls=distribution([report.tool_calls for report in reports]),
        input_tokens=distribution(
            [
                report.input_tokens
                for report in reports
                if getattr(report, "input_tokens", None) is not None
            ]
        ),
        output_tokens=distribution(
            [
                report.output_tokens
                for report in reports
                if getattr(report, "output_tokens", None) is not None
            ]
        ),
        total_tokens=distribution(
            [report.total_tokens for report in reports if report.total_tokens is not None]
        ),
        total_cost_usd=sum(prices) if prices else None,
        priced_run_count=len(prices),
        failure_counts=dict(sorted(failures.items())),
    )


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def compare_baseline(current: SuiteMetrics, baseline: SuiteMetrics) -> BaselineComparison:
    current_failures = _hard_failure_count(current.overall)
    baseline_failures = _hard_failure_count(baseline.overall)
    current_cohorts = set(current.by_model_case)
    baseline_cohorts = set(baseline.by_model_case)
    pairs = {
        "task_success_rate": (
            current.overall.task_success_rate,
            baseline.overall.task_success_rate,
        ),
        "harness_integrity_rate": (
            current.overall.harness_integrity_rate,
            baseline.overall.harness_integrity_rate,
        ),
        "provider_success_rate": (
            current.overall.provider_success_rate,
            baseline.overall.provider_success_rate,
        ),
        "tool_dispatch_success_rate": (
            current.overall.tool_dispatch_success_rate,
            baseline.overall.tool_dispatch_success_rate,
        ),
        "duration_ms_mean": (
            _mean(current.overall.duration_ms),
            _mean(baseline.overall.duration_ms),
        ),
        "total_tokens_mean": (
            _mean(current.overall.total_tokens),
            _mean(baseline.overall.total_tokens),
        ),
    }
    deltas = {
        name: _delta(current_value, baseline_value)
        for name, (current_value, baseline_value) in pairs.items()
        if current_value is not None and baseline_value is not None
    }
    return BaselineComparison(
        hard_regression=current_failures > baseline_failures,
        overall=deltas,
        missing_cohorts=tuple(sorted(baseline_cohorts - current_cohorts)),
        new_cohorts=tuple(sorted(current_cohorts - baseline_cohorts)),
    )


def _hard_failure_count(metrics: CohortMetrics) -> int:
    return metrics.failure_counts.get("harness_error", 0) + metrics.failure_counts.get(
        "safety_violation", 0
    )


def _mean(value: Distribution | None) -> float | None:
    return value.mean if value is not None else None


def _delta(current: float, baseline: float) -> MetricDelta:
    absolute = current - baseline
    relative = absolute / abs(baseline) if baseline else None
    return MetricDelta(current, baseline, absolute, relative)

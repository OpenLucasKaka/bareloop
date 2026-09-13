from types import SimpleNamespace

import pytest

from bareloop.eval.metrics import aggregate_reports, compare_baseline, distribution


def report(
    *,
    model: str,
    passed: bool,
    classification: str,
    duration_ms: float,
    rounds: int,
    tool_calls: int,
    provider_requests: int,
    provider_successes: int,
    provider_latency_ms: list[float] | None = None,
    tool_successes: int = 0,
    tool_errors: int = 0,
    tool_blocked: int = 0,
    invalid_tool_calls: int = 0,
    recovered_tool_error: bool = False,
    total_tokens: int | None = None,
    cost_usd: float | None = None,
):
    return SimpleNamespace(
        model=model,
        case="case-a",
        passed=passed,
        classification=classification,
        duration_ms=duration_ms,
        rounds=rounds,
        tool_calls=tool_calls,
        provider_requests=provider_requests,
        provider_successes=provider_successes,
        provider_latency_ms=provider_latency_ms or [],
        tool_successes=tool_successes,
        tool_errors=tool_errors,
        tool_blocked=tool_blocked,
        invalid_tool_calls=invalid_tool_calls,
        recovered_tool_error=recovered_tool_error,
        total_tokens=total_tokens,
        cost_usd=cost_usd,
    )


def test_distribution_uses_nearest_rank_p95_and_population_stddev() -> None:
    result = distribution([10, 20, 30, 40, 50])

    assert result.count == 5
    assert result.mean == 30
    assert result.median == 30
    assert result.p95 == 50
    assert result.minimum == 10
    assert result.maximum == 50
    assert result.stddev == pytest.approx(14.1421356)
    assert distribution([]) is None


def test_aggregate_separates_harness_stability_from_task_success() -> None:
    reports = [
        report(
            model="model-a",
            passed=True,
            classification="passed",
            duration_ms=10,
            rounds=1,
            tool_calls=1,
            provider_requests=2,
            provider_successes=2,
            provider_latency_ms=[5, 10],
            tool_successes=1,
            total_tokens=100,
            cost_usd=0.1,
        ),
        report(
            model="model-a",
            passed=True,
            classification="passed",
            duration_ms=20,
            rounds=2,
            tool_calls=2,
            provider_requests=1,
            provider_successes=1,
            provider_latency_ms=[20],
            tool_successes=1,
            tool_errors=1,
            recovered_tool_error=True,
            total_tokens=200,
            cost_usd=0.2,
        ),
        report(
            model="model-b",
            passed=False,
            classification="harness_error",
            duration_ms=30,
            rounds=3,
            tool_calls=0,
            provider_requests=0,
            provider_successes=0,
        ),
    ]

    metrics = aggregate_reports(reports)

    assert metrics.overall.run_count == 3
    assert metrics.overall.task_success_rate == pytest.approx(2 / 3)
    assert metrics.overall.harness_integrity_rate == pytest.approx(2 / 3)
    assert metrics.overall.provider_success_rate == 1.0
    assert metrics.overall.provider_latency_ms.p95 == 20
    assert metrics.overall.tool_dispatch_success_rate == pytest.approx(2 / 3)
    assert metrics.overall.invalid_tool_call_rate == 0.0
    assert metrics.overall.max_rounds_rate == 0.0
    assert metrics.overall.tool_error_recovery_rate == 1.0
    assert metrics.overall.duration_ms.p95 == 30
    assert metrics.overall.total_tokens.count == 2
    assert metrics.overall.total_cost_usd == pytest.approx(0.3)
    assert metrics.overall.failure_counts == {"harness_error": 1}
    assert set(metrics.by_model) == {"model-a", "model-b"}
    assert metrics.by_model["model-a"].task_success_rate == 1.0


def test_baseline_comparison_flags_new_harness_failure() -> None:
    baseline = aggregate_reports(
        [
            report(
                model="model-a",
                passed=True,
                classification="passed",
                duration_ms=10,
                rounds=1,
                tool_calls=0,
                provider_requests=1,
                provider_successes=1,
            )
        ]
    )
    current = aggregate_reports(
        [
            report(
                model="model-a",
                passed=False,
                classification="harness_error",
                duration_ms=12,
                rounds=1,
                tool_calls=0,
                provider_requests=1,
                provider_successes=1,
            )
        ]
    )

    comparison = compare_baseline(current, baseline)

    assert comparison.hard_regression is True
    assert comparison.overall["task_success_rate"].absolute == -1.0
    assert comparison.overall["duration_ms_mean"].absolute == 2.0

from __future__ import annotations

import csv
import html as html_module
import io
import json
from dataclasses import asdict, dataclass, is_dataclass
from pathlib import Path
from types import SimpleNamespace
from typing import Any

from bareloop.eval.metrics import BaselineComparison, SuiteMetrics, aggregate_reports

SCHEMA_VERSION = 1


@dataclass(frozen=True)
class ReportPaths:
    json: Path
    csv: Path
    html: Path


def load_baseline_metrics(path: str | Path) -> SuiteMetrics:
    try:
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"could not load baseline report {path}: {exc}") from exc
    if not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA_VERSION:
        raise ValueError(f"baseline report must use schema version {SCHEMA_VERSION}")
    runs = payload.get("runs")
    if not isinstance(runs, list) or not runs or not all(isinstance(run, dict) for run in runs):
        raise ValueError("baseline report must contain non-empty raw runs")
    try:
        return aggregate_reports([SimpleNamespace(**run) for run in runs])
    except (AttributeError, TypeError, ValueError) as exc:
        raise ValueError(f"baseline report contains invalid run data: {exc}") from exc


def write_report_artifacts(
    output_dir: str | Path,
    *,
    reports: list[Any],
    metrics: SuiteMetrics,
    metadata: dict[str, Any],
    baseline: BaselineComparison | None = None,
) -> ReportPaths:
    directory = Path(output_dir)
    directory.mkdir(parents=True, exist_ok=True)
    paths = ReportPaths(
        json=directory / "report.json",
        csv=directory / "runs.csv",
        html=directory / "report.html",
    )
    run_dicts = [_as_dict(report) for report in reports]
    payload = {
        "schema_version": SCHEMA_VERSION,
        "metadata": metadata,
        "runs": run_dicts,
        "metrics": metrics.to_dict(),
        "baseline": baseline.to_dict() if baseline is not None else None,
    }
    _atomic_write(paths.json, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")
    _atomic_write(paths.csv, _render_csv(run_dicts))
    _atomic_write(paths.html, _render_html(payload, metrics))
    return paths


def _as_dict(value: Any) -> dict[str, Any]:
    if hasattr(value, "to_dict"):
        return value.to_dict()
    if is_dataclass(value):
        return asdict(value)
    return dict(vars(value))


def _atomic_write(path: Path, content: str) -> None:
    temporary = path.with_suffix(f"{path.suffix}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def _render_csv(runs: list[dict[str, Any]]) -> str:
    if not runs:
        return ""
    preferred = [
        "model",
        "case",
        "repetition",
        "passed",
        "classification",
        "duration_ms",
        "rounds",
        "tool_calls",
        "provider_requests",
        "provider_successes",
        "provider_latency_ms",
        "input_tokens",
        "output_tokens",
        "total_tokens",
        "cost_usd",
        "tool_successes",
        "tool_errors",
        "tool_blocked",
        "invalid_tool_calls",
        "security_blocks",
        "safety_violations",
        "error",
        "final_output",
    ]
    all_fields = set().union(*(run.keys() for run in runs))
    fields = [field for field in preferred if field in all_fields]
    fields.extend(sorted(all_fields - set(fields)))
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=fields)
    writer.writeheader()
    for run in runs:
        writer.writerow({field: _csv_value(run.get(field)) for field in fields})
    return buffer.getvalue()


def _csv_value(value: Any) -> Any:
    if value is None:
        return ""
    if isinstance(value, (dict, list, tuple)):
        return json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return value


def _render_html(payload: dict[str, Any], metrics: SuiteMetrics) -> str:
    metadata_rows = "".join(
        f"<tr><th>{_escape(key)}</th><td>{_escape(value)}</td></tr>"
        for key, value in payload["metadata"].items()
    )
    stability = []
    failures = []
    latency = []
    tokens = []
    cost = []
    rounds = []
    tools = []
    for model, cohort in metrics.by_model.items():
        stability.extend(
            [
                (f"{model} task", _percent(cohort.task_success_rate)),
                (f"{model} harness", _percent(cohort.harness_integrity_rate)),
            ]
        )
        failures.append((model, float(sum(cohort.failure_counts.values()))))
        latency.extend(
            [
                (f"{model} run", _mean(cohort.duration_ms)),
                (f"{model} provider", _mean(cohort.provider_latency_ms)),
            ]
        )
        tokens.append((model, _mean(cohort.total_tokens)))
        cost.append((model, float(cohort.total_cost_usd or 0)))
        rounds.append((model, _mean(cohort.rounds)))
        tools.append((model, _mean(cohort.tool_calls)))
    run_rows = "".join(_run_row(run) for run in payload["runs"])
    overall = metrics.overall
    summary = (
        f"Runs: {overall.run_count} · Task success: {_display_rate(overall.task_success_rate)} · "
        f"Harness integrity: {_display_rate(overall.harness_integrity_rate)} · "
        f"Provider success: {_display_rate(overall.provider_success_rate)}"
    )
    stability_chart = _bar_chart("Stability percent", stability, 100)
    failure_chart = _bar_chart("Failures", failures)
    latency_chart = _bar_chart("Mean duration (ms)", latency)
    token_chart = _bar_chart("Mean total tokens", tokens)
    cost_chart = _bar_chart("Total configured cost (USD)", cost)
    round_chart = _bar_chart("Mean rounds", rounds)
    tool_chart = _bar_chart("Mean Tool calls", tools)
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>BareLoop Harness Stability Eval</title>
<style>
body{{font:14px system-ui,sans-serif;margin:32px;background:#f6f7fb;color:#172033}}
main{{max-width:1180px;margin:auto}}
section{{background:white;padding:20px;margin:16px 0;border-radius:12px}}
h1,h2{{margin-top:0}}
table{{border-collapse:collapse;width:100%}}
th,td{{padding:8px;border-bottom:1px solid #ddd;text-align:left;vertical-align:top}}
.charts{{display:grid;grid-template-columns:repeat(auto-fit,minmax(360px,1fr));gap:16px}}
svg{{width:100%;height:auto}}
.muted{{color:#607089}}
</style>
</head>
<body><main>
<h1>BareLoop Harness Stability Eval</h1><p>{_escape(summary)}</p>
<section><h2>Provenance</h2><table>{metadata_rows}</table></section>
<section id="stability"><h2>Task and harness stability</h2>{stability_chart}</section>
<section id="failures"><h2>Failure classifications</h2>{failure_chart}</section>
<section id="latency"><h2>Latency</h2>{latency_chart}</section>
<section id="tokens-cost"><h2>Tokens and cost</h2>
<div class="charts">{token_chart}{cost_chart}</div></section>
<section id="rounds-tools"><h2>Rounds and Tool calls</h2>
<div class="charts">{round_chart}{tool_chart}</div></section>
<section><h2>Raw runs</h2>
<p class="muted">p95 values are exploratory for five-run cohorts.</p>
<table><thead><tr>
<th>Model</th><th>Case</th><th>Run</th><th>Class</th><th>ms</th>
<th>Rounds</th><th>Tools</th><th>Tokens</th><th>Cost</th><th>Output / error</th>
</tr></thead><tbody>{run_rows}</tbody></table></section>
</main></body></html>
"""


def _bar_chart(title: str, values: list[tuple[str, float]], fixed_max: float | None = None) -> str:
    width = 600
    row_height = 34
    height = max(80, 44 + row_height * len(values))
    maximum = fixed_max or max((value for _, value in values), default=1) or 1
    rows = []
    for index, (label, value) in enumerate(values):
        y = 30 + index * row_height
        bar_width = max(0, min(400, 400 * value / maximum))
        rows.append(
            f'<text x="4" y="{y + 14}" font-size="12">{_escape(label)}</text>'
            f'<rect x="150" y="{y}" width="{bar_width:.2f}" height="20" fill="#4f67d8" rx="3"/>'
            f'<text x="{155 + bar_width:.2f}" y="{y + 14}" font-size="12">{value:.3g}</text>'
        )
    return (
        f'<svg viewBox="0 0 {width} {height}" role="img" aria-label="{_escape(title)}">'
        f"<title>{_escape(title)}</title>{''.join(rows)}</svg>"
    )


def _run_row(run: dict[str, Any]) -> str:
    output = run.get("error") or run.get("final_output") or ""
    cells = [
        run.get("model"),
        run.get("case"),
        run.get("repetition", 1),
        run.get("classification"),
        run.get("duration_ms"),
        run.get("rounds"),
        run.get("tool_calls"),
        run.get("total_tokens"),
        run.get("cost_usd"),
        output,
    ]
    return "<tr>" + "".join(f"<td>{_escape(value)}</td>" for value in cells) + "</tr>"


def _escape(value: Any) -> str:
    return html_module.escape("" if value is None else str(value), quote=True)


def _percent(value: float | None) -> float:
    return 0 if value is None else value * 100


def _display_rate(value: float | None) -> str:
    return "unavailable" if value is None else f"{value:.1%}"


def _mean(value: Any) -> float:
    return 0 if value is None else float(value.mean)

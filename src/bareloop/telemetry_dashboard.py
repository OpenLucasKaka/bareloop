from __future__ import annotations

import argparse
import json
import math
import re
from collections import Counter
from collections.abc import Iterable
from html import escape
from pathlib import Path
from statistics import mean, median
from typing import Any

START_MARKER = "<!-- telemetry-dashboard:start -->"
END_MARKER = "<!-- telemetry-dashboard:end -->"


def load_records(path: str | Path) -> list[dict[str, Any]]:
    input_path = Path(path)
    if not input_path.exists():
        return []
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(input_path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as error:
            raise ValueError(f"invalid telemetry JSON on line {line_number}: {error}") from error
        if not isinstance(record, dict):
            raise ValueError(f"telemetry line {line_number} must contain a JSON object")
        records.append(record)
    return records


def aggregate_records(records: Iterable[dict[str, Any]]) -> dict[str, Any]:
    rows = list(records)
    provider_requests = sum(_number(row, "provider_requests") for row in rows)
    provider_successes = sum(_number(row, "provider_successes") for row in rows)
    tool_calls = sum(_number(row, "tool_calls") for row in rows)
    tool_successes = sum(_number(row, "tool_successes") for row in rows)
    latencies = [
        float(value)
        for row in rows
        for value in row.get("provider_latency_ms", [])
        if isinstance(value, (int, float))
    ]
    token_rows = [
        row
        for row in rows
        if row.get("token_usage_available")
        and all(
            isinstance(row.get(name), int)
            for name in ("input_tokens", "output_tokens", "total_tokens")
        )
    ]
    recorded_at = [str(row["recorded_at"]) for row in rows if row.get("recorded_at")]
    return {
        "run_count": len(rows),
        "completion_rate": _rate(
            sum(row.get("termination") == "completed" for row in rows), len(rows)
        ),
        "provider_requests": provider_requests,
        "provider_successes": provider_successes,
        "provider_success_rate": _rate(provider_successes, provider_requests),
        "provider_latency_ms": _distribution(latencies),
        "token_runs": len(token_rows),
        "input_tokens": sum(int(row["input_tokens"]) for row in token_rows),
        "output_tokens": sum(int(row["output_tokens"]) for row in token_rows),
        "total_tokens": sum(int(row["total_tokens"]) for row in token_rows),
        "tool_calls": tool_calls,
        "tool_successes": tool_successes,
        "tool_success_rate": _rate(tool_successes, tool_calls),
        "tool_errors": sum(_number(row, "tool_errors") for row in rows),
        "tool_blocked": sum(_number(row, "tool_blocked") for row in rows),
        "invalid_tool_calls": sum(_number(row, "invalid_tool_calls") for row in rows),
        "security_blocks": sum(_number(row, "security_blocks") for row in rows),
        "safety_violations": sum(_number(row, "safety_violations") for row in rows),
        "termination_counts": dict(Counter(str(row.get("termination", "unknown")) for row in rows)),
        "last_updated": max(recorded_at) if recorded_at else None,
    }


def render_markdown(summary: dict[str, Any], *, source: str) -> str:
    latency = summary["provider_latency_ms"]
    rows = [
        ("Run count", str(summary["run_count"])),
        ("Completion rate", _percent(summary["completion_rate"])),
        ("Provider success rate", _percent(summary["provider_success_rate"])),
        ("Provider latency (mean / P50 / P95)", _distribution_text(latency)),
        ("Token usage (runs with provider usage)", str(summary["token_runs"])),
        (
            "Input / output / total tokens",
            f"{summary['input_tokens']} / {summary['output_tokens']} / {summary['total_tokens']}",
        ),
        (
            "Tool calls (success rate)",
            f"{summary['tool_calls']} ({_percent(summary['tool_success_rate'])})",
        ),
        (
            "Tool errors / blocked / invalid",
            (
                f"{summary['tool_errors']} / {summary['tool_blocked']} / "
                f"{summary['invalid_tool_calls']}"
            ),
        ),
        (
            "Security blocks / safety violations",
            f"{summary['security_blocks']} / {summary['safety_violations']}",
        ),
        ("Terminations", _termination_text(summary["termination_counts"])),
        ("Last updated", summary["last_updated"] or "N/A"),
    ]
    table = "| Metric | Value |\n| --- | --- |\n" + "\n".join(
        f"| {name} | {value} |" for name, value in rows
    )
    note = (
        (
            f"_Generated from `{source}`. Token totals include only records with "
            "provider-reported usage._"
        )
        if summary["run_count"]
        else "_No telemetry runs recorded. Run the dashboard command to populate this section._"
    )
    return (
        "### Runtime telemetry\n\n"
        "![Runtime telemetry dashboard](docs/assets/telemetry-dashboard.svg)\n\n"
        f"{table}\n\n{note}"
    )


def render_svg(summary: dict[str, Any]) -> str:
    metrics = [
        ("Completion", _percentage_value(summary["completion_rate"])),
        ("Provider", _percentage_value(summary["provider_success_rate"])),
        ("Tools", _percentage_value(summary["tool_success_rate"])),
        ("Safety", 100.0 if summary["safety_violations"] == 0 and summary["run_count"] else 0.0),
    ]
    width, height = 720, 300
    chart_left, chart_top, chart_height = 64, 42, 190
    bar_width = 100
    gap = 46
    max_value = 100.0
    bars = []
    for index, (label, value) in enumerate(metrics):
        x = chart_left + index * (bar_width + gap) + 22
        bar_height = chart_height * value / max_value
        y = chart_top + chart_height - bar_height
        bars.append(
            f'<rect x="{x}" y="{y:.1f}" width="{bar_width}" height="{bar_height:.1f}" '
            f'rx="8" fill="#22d3ee"><title>{escape(label)}: {value:.1f}%</title></rect>'
        )
        bars.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{chart_top + chart_height + 28}" '
            f'text-anchor="middle" fill="#cbd5e1" font-size="14">{escape(label)}</text>'
        )
        bars.append(
            f'<text x="{x + bar_width / 2:.1f}" y="{max(y - 8, 26):.1f}" '
            f'text-anchor="middle" fill="#f8fafc" font-size="15">{value:.1f}%</text>'
        )
    return (
        f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 {width} {height}" role="img" '
        'aria-labelledby="telemetry-title telemetry-desc">'
        '<title id="telemetry-title">BareLoop runtime telemetry</title>'
        '<desc id="telemetry-desc">Completion, provider, tool, and safety success rates.</desc>'
        '<rect width="100%" height="100%" rx="16" fill="#0f172a"/>'
        '<text x="32" y="28" fill="#f8fafc" font-size="17" '
        'font-family="-apple-system,BlinkMacSystemFont,Segoe UI,sans-serif">'
        "Runtime telemetry</text>" + "".join(bars) + "</svg>"
    )


def update_readme(path: str | Path, markdown: str) -> None:
    readme_path = Path(path)
    content = readme_path.read_text(encoding="utf-8") if readme_path.exists() else ""
    block = f"{START_MARKER}\n{markdown}\n{END_MARKER}"
    has_start, has_end = START_MARKER in content, END_MARKER in content
    if has_start != has_end:
        raise ValueError("README telemetry markers must appear as a pair")
    if has_start:
        content = re.sub(
            re.escape(START_MARKER) + r".*?" + re.escape(END_MARKER),
            block,
            content,
            count=1,
            flags=re.S,
        )
    else:
        content = content.rstrip() + "\n\n" + block + "\n"
    _atomic_write(readme_path, content)


def generate_dashboard(
    input_path: str | Path, readme_path: str | Path, svg_path: str | Path
) -> dict[str, Any]:
    summary = aggregate_records(load_records(input_path))
    _atomic_write(Path(svg_path), render_svg(summary))
    update_readme(readme_path, render_markdown(summary, source=str(input_path)))
    return summary


def _number(row: dict[str, Any], key: str) -> int:
    value = row.get(key, 0)
    return int(value) if isinstance(value, (int, float)) else 0


def _rate(numerator: int, denominator: int) -> float | None:
    return numerator / denominator if denominator else None


def _distribution(values: list[float]) -> dict[str, float | int] | None:
    if not values:
        return None
    ordered = sorted(values)
    return {
        "count": len(ordered),
        "mean": mean(ordered),
        "median": median(ordered),
        "p95": ordered[max(0, math.ceil(len(ordered) * 0.95) - 1)],
    }


def _percentage_value(value: float | None) -> float:
    return value * 100 if value is not None else 0.0


def _percent(value: float | None) -> str:
    return "N/A" if value is None else f"{value * 100:.1f}%"


def _distribution_text(value: dict[str, Any] | None) -> str:
    return (
        "N/A"
        if value is None
        else f"{value['mean']:.1f} / {value['median']:.1f} / {value['p95']:.1f} ms"
    )


def _termination_text(value: dict[str, int]) -> str:
    return ", ".join(f"{key}: {count}" for key, count in sorted(value.items())) or "N/A"


def _atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    temporary.write_text(content, encoding="utf-8")
    temporary.replace(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Generate README telemetry dashboard")
    parser.add_argument("--input", type=Path, required=True, help="telemetry JSONL path")
    parser.add_argument("--readme", type=Path, default=Path("README.md"))
    parser.add_argument("--svg", type=Path, default=Path("docs/assets/telemetry-dashboard.svg"))
    args = parser.parse_args(argv)
    summary = generate_dashboard(args.input, args.readme, args.svg)
    print(f"Updated {args.readme} and {args.svg} from {summary['run_count']} telemetry runs")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

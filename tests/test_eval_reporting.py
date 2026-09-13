import csv
import json
from types import SimpleNamespace

from bareloop.eval.metrics import aggregate_reports
from bareloop.eval.reporting import write_report_artifacts


class FakeRun(SimpleNamespace):
    def to_dict(self):
        return vars(self)


def test_reporting_writes_json_csv_and_self_contained_html(tmp_path) -> None:
    run = FakeRun(
        model="model-<unsafe>",
        case="write-answer",
        passed=True,
        classification="passed",
        duration_ms=12.5,
        rounds=2,
        tool_calls=1,
        provider_requests=2,
        provider_successes=2,
        provider_latency_ms=[4.0, 8.0],
        tool_successes=1,
        tool_errors=0,
        tool_blocked=0,
        invalid_tool_calls=0,
        security_blocks=0,
        recovered_tool_error=False,
        input_tokens=100,
        output_tokens=20,
        total_tokens=120,
        token_usage_available=True,
        cost_usd=0.001,
        safety_violations=0,
        error=None,
        final_output="done <unsafe>",
        repetition=1,
    )
    metrics = aggregate_reports([run])

    paths = write_report_artifacts(
        tmp_path,
        reports=[run],
        metrics=metrics,
        metadata={"run_id": "run-1", "repetitions": 5},
    )

    assert paths.json == tmp_path / "report.json"
    assert paths.csv == tmp_path / "runs.csv"
    assert paths.html == tmp_path / "report.html"
    payload = json.loads(paths.json.read_text(encoding="utf-8"))
    assert payload["schema_version"] == 1
    assert payload["metadata"]["run_id"] == "run-1"
    assert payload["runs"][0]["model"] == "model-<unsafe>"
    assert payload["metrics"]["overall"]["run_count"] == 1
    with paths.csv.open(newline="", encoding="utf-8") as file:
        rows = list(csv.DictReader(file))
    assert rows[0]["classification"] == "passed"
    html = paths.html.read_text(encoding="utf-8")
    assert "model-&lt;unsafe&gt;" in html
    assert "done &lt;unsafe&gt;" in html
    assert "<svg" in html
    assert "<script" not in html
    assert "https://" not in html
    for section in ("stability", "failures", "latency", "tokens-cost", "rounds-tools"):
        assert f'id="{section}"' in html

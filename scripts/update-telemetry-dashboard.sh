#!/usr/bin/env bash
set -euo pipefail

script_dir="$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)"
repo_root="$(CDPATH= cd -- "$script_dir/.." && pwd)"
cd "$repo_root"

repetitions="${REPETITIONS:-5}"
output_dir="${OUTPUT_DIR:-.bareloop/eval/runs/$(date -u +%Y%m%dT%H%M%SZ)}"

uv run python -m bareloop.eval \
  --suite \
  --all-configured-models \
  --repetitions "$repetitions" \
  --output-dir "$output_dir"

uv run python -m bareloop.telemetry_dashboard \
  --input "$output_dir/telemetry.jsonl" \
  --readme README.md \
  --svg docs/assets/telemetry-dashboard.svg

echo "Telemetry dashboard refreshed from $output_dir"

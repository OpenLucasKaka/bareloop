from __future__ import annotations

import argparse
import sys
from pathlib import Path

from bareloop.eval.swebench.models import load_swebench_instances
from bareloop.eval.swebench.runner import run_swebench_suite


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run BareLoop SWE-bench evaluation harness")
    parser.add_argument(
        "--dataset-path",
        type=Path,
        required=True,
        help="Path to SWE-bench dataset (JSON or JSONL format)",
    )
    parser.add_argument(
        "--repo-dir",
        type=Path,
        required=True,
        help="Base git repository directory for the target project",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path(".bareloop/eval/swebench"),
        help="Directory to save predictions.jsonl and report.json",
    )
    parser.add_argument(
        "--instance-id",
        action="append",
        help="Filter specific instance ID(s) to run (repeatable)",
    )
    parser.add_argument(
        "--model",
        type=str,
        help="Model ID to evaluate against (defaults to PRIMARY_MODEL)",
    )
    parser.add_argument(
        "--max-rounds",
        type=int,
        default=15,
        help="Maximum loop rounds per instance (default: 15)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        help="Limit number of instances to evaluate",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)

    try:
        instances = load_swebench_instances(args.dataset_path)
    except Exception as exc:
        print(f"error loading dataset: {exc}", file=sys.stderr)
        return 2

    if args.instance_id:
        filter_ids = set(args.instance_id)
        instances = [inst for inst in instances if inst.instance_id in filter_ids]

    if args.limit and args.limit > 0:
        instances = instances[: args.limit]

    if not instances:
        print("warning: no matching instances found to evaluate", file=sys.stderr)
        return 0

    print(f"Loaded {len(instances)} SWE-bench instance(s). Starting BareLoop evaluation...")
    summary = run_swebench_suite(
        instances,
        base_repo_dir=args.repo_dir,
        output_dir=args.output_dir,
        model=args.model,
        max_rounds=args.max_rounds,
    )
    print(
        f"Evaluation finished! Patches generated: "
        f"{summary['patches_generated']}/{summary['total_instances']} "
        f"({summary['patch_generation_rate']:.1%})"
    )
    print(f"Predictions saved to: {summary['output_predictions']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

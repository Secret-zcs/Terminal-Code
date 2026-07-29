from __future__ import annotations

import argparse
import json
from pathlib import Path

from mewcode.evolution.benchmark import (
    compare_skill_variants,
    render_markdown_report,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run deterministic self-evolution dataset evaluation.",
    )
    parser.add_argument(
        "--dataset",
        default="benchmarks/self_evolution_seed_cases.jsonl",
        help="JSONL benchmark dataset path.",
    )
    parser.add_argument(
        "--json-output",
        default="",
        help="Optional path for structured JSON results.",
    )
    parser.add_argument(
        "--md-output",
        default="",
        help="Optional path for Markdown report.",
    )
    args = parser.parse_args()

    result = compare_skill_variants(args.dataset)
    if args.json_output:
        output_path = Path(args.json_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    report = render_markdown_report(result)
    if args.md_output:
        output_path = Path(args.md_output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(report, encoding="utf-8")
    else:
        print(report, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

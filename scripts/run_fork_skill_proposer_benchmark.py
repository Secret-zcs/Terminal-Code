from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mewcode.client import create_client
from mewcode.config import ConfigError, load_config
from mewcode.evolution.proposer_benchmark import (
    render_proposer_benchmark_markdown,
    run_proposer_benchmark,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run real-provider Fork Skill Proposer benchmark.",
    )
    parser.add_argument(
        "--dataset",
        default="benchmarks/oasst1_derived_cases.jsonl",
    )
    parser.add_argument("--max-cases", type=int, default=10)
    parser.add_argument("--config", default="")
    parser.add_argument("--provider-index", type=int, default=0)
    parser.add_argument("--json-output", default="")
    parser.add_argument("--md-output", default="")
    args = parser.parse_args()

    try:
        config = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc
    try:
        provider = config.providers[args.provider_index]
    except IndexError as exc:
        raise SystemExit(f"provider index not found: {args.provider_index}") from exc
    try:
        client = create_client(provider)
    except Exception as exc:
        raise SystemExit(f"provider initialization failed: {exc}") from exc
    result = asyncio.run(run_proposer_benchmark(
        client,
        args.dataset,
        max_cases=args.max_cases,
    ))
    rendered = render_proposer_benchmark_markdown(result)
    if args.json_output:
        output = Path(args.json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if args.md_output:
        output = Path(args.md_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

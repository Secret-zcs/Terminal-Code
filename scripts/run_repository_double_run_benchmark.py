from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from mewcode.client import create_client
from mewcode.config import ConfigError, load_config
from mewcode.evolution.benchmark import DEFAULT_EVOLVED_SKILL
from mewcode.evolution.repository_benchmark import (
    analyze_repository_benchmark_failures,
    analyze_repository_route_impacts,
    promoted_route_families_from_benchmark,
    recompute_repository_task_router_policy,
    render_repository_benchmark_markdown,
    render_repository_benchmark_resume_summary,
    run_repository_double_run_benchmark,
    summarize_repository_benchmark_metrics,
)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Run baseline/evolved repository double-run benchmark.",
    )
    parser.add_argument("--fixtures", default="fixtures/repository_double_run")
    parser.add_argument("--candidate-skill", default="")
    parser.add_argument("--config", default="")
    parser.add_argument("--provider-index", type=int, default=0)
    parser.add_argument("--workspace-root", default="")
    parser.add_argument("--max-cases", type=int, default=0)
    parser.add_argument(
        "--case-id",
        action="append",
        default=[],
        help="Run only a specific fixture case id; can be passed multiple times.",
    )
    parser.add_argument(
        "--case-ids-file",
        default="",
        help="Read fixture case ids from a newline-delimited file.",
    )
    parser.add_argument(
        "--case-bucket",
        action="append",
        default=[],
        help="Run case ids from a failure taxonomy bucket in --reuse-baseline-json.",
    )
    parser.add_argument("--max-iterations", type=int, default=20)
    parser.add_argument(
        "--evolved-max-iterations",
        type=int,
        default=0,
        help="Override max iterations for evolved candidate runs only.",
    )
    parser.add_argument("--test-timeout", type=float, default=120.0)
    parser.add_argument(
        "--from-json",
        default="",
        help="Read an existing benchmark JSON and render metrics without running a provider.",
    )
    parser.add_argument(
        "--recompute-task-router-policy",
        action="store_true",
        help="With --from-json, recompute a promoted task-router policy without provider calls.",
    )
    parser.add_argument(
        "--reuse-baseline-json",
        default="",
        help="Reuse baseline case results from an existing JSON and run evolved only.",
    )
    parser.add_argument(
        "--strategy-router",
        action="store_true",
        help="Append fixture-family strategy hints to evolved candidate runs.",
    )
    parser.add_argument(
        "--task-router",
        action="store_true",
        help="Route evolved runs to built-in family-specific Skills or skip injection.",
    )
    parser.add_argument(
        "--task-router-rerun-skips",
        action="store_true",
        help="Rerun no-skill task-router skips instead of short-circuiting to baseline policy.",
    )
    parser.add_argument(
        "--task-router-promoted-family",
        action="append",
        default=None,
        help="Only inject routed Skills for this promoted family; can be passed multiple times.",
    )
    parser.add_argument(
        "--task-router-promoted-families-from-json",
        action="append",
        default=[],
        help="Load promoted route families from prior benchmark JSON; can be passed multiple times.",
    )
    parser.add_argument(
        "--task-router-promotion-min-cases",
        type=int,
        default=1,
        help="Minimum injected cases required before auto-promoting a route family.",
    )
    parser.add_argument(
        "--task-router-promotion-require-efficiency",
        action="store_true",
        help="Auto-promote only route families with non-increasing calls, tokens, and elapsed time.",
    )
    parser.add_argument("--json-output", default="")
    parser.add_argument("--md-output", default="")
    parser.add_argument("--summary-output", default="")
    args = parser.parse_args()

    if args.from_json:
        result = _load_existing_result(Path(args.from_json))
        if args.recompute_task_router_policy:
            task_router_promoted_families = _load_promoted_route_families(
                args.task_router_promoted_family,
                args.task_router_promoted_families_from_json or [args.from_json],
                min_cases=args.task_router_promotion_min_cases,
                require_runtime_efficiency=args.task_router_promotion_require_efficiency,
            )
            result = recompute_repository_task_router_policy(
                result,
                promoted_families=task_router_promoted_families,
                min_cases=args.task_router_promotion_min_cases,
                require_runtime_efficiency=args.task_router_promotion_require_efficiency,
                short_circuit_skips=not args.task_router_rerun_skips,
            )
            result["configuration"]["policy_recompute_source"] = args.from_json
            result["configuration"]["task_router_promotion_sources"] = tuple(
                args.task_router_promoted_families_from_json or [args.from_json]
            )
        _write_outputs(result, args.json_output, args.md_output, args.summary_output)
        if not args.md_output:
            print(render_repository_benchmark_markdown(result), end="")
        return 0

    candidate_source = "default-evolved-skill"
    candidate_skill = DEFAULT_EVOLVED_SKILL
    if args.candidate_skill:
        candidate_path = Path(args.candidate_skill)
        try:
            candidate_skill = candidate_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise SystemExit(f"candidate skill read failed: {exc}") from exc
        candidate_source = str(candidate_path)

    reuse_baseline_result = None
    if args.reuse_baseline_json:
        reuse_baseline_result = _load_existing_result(Path(args.reuse_baseline_json))
    case_ids = tuple(_load_case_ids(
        args.case_id,
        args.case_ids_file,
        args.case_bucket,
        reuse_baseline_result,
    ))
    task_router_promoted_families = _load_promoted_route_families(
        args.task_router_promoted_family,
        args.task_router_promoted_families_from_json,
        min_cases=args.task_router_promotion_min_cases,
        require_runtime_efficiency=args.task_router_promotion_require_efficiency,
    )

    try:
        config = load_config(Path(args.config) if args.config else None)
    except ConfigError as exc:
        raise SystemExit(f"configuration error: {exc}") from exc
    try:
        provider = config.providers[args.provider_index]
    except IndexError as exc:
        raise SystemExit(f"provider index not found: {args.provider_index}") from exc
    try:
        create_client(provider)
    except Exception as exc:
        raise SystemExit(f"provider initialization failed: {exc}") from exc

    def client_factory(_repository_root: Path, _evolved: bool):
        return create_client(provider)

    result = asyncio.run(run_repository_double_run_benchmark(
        client_factory,
        args.fixtures,
        candidate_skill,
        workspace_root=args.workspace_root or None,
        protocol=provider.protocol,
        max_iterations=args.max_iterations,
        evolved_max_iterations=args.evolved_max_iterations or None,
        test_timeout_seconds=args.test_timeout,
        max_cases=args.max_cases or None,
        reuse_baseline_result=reuse_baseline_result,
        case_ids=case_ids,
        strategy_router_enabled=args.strategy_router,
        task_router_enabled=args.task_router,
        task_router_short_circuit_skips=not args.task_router_rerun_skips,
        task_router_promoted_families=task_router_promoted_families,
    ))
    result["configuration"]["candidate_skill_source"] = candidate_source
    if args.reuse_baseline_json:
        result["configuration"]["baseline_reuse_source"] = args.reuse_baseline_json
    if task_router_promoted_families is not None:
        result["configuration"]["task_router_promotion_sources"] = tuple(
            args.task_router_promoted_families_from_json
        )
        result["configuration"]["task_router_promotion_min_cases"] = (
            args.task_router_promotion_min_cases
        )
        result["configuration"]["task_router_promotion_require_efficiency"] = (
            args.task_router_promotion_require_efficiency
        )
    _write_outputs(result, args.json_output, args.md_output, args.summary_output)
    if not args.md_output:
        print(render_repository_benchmark_markdown(result), end="")
    return 0


def _load_existing_result(path: Path) -> dict:
    try:
        result = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"benchmark JSON read failed: {exc}") from exc
    if not isinstance(result, dict):
        raise SystemExit("benchmark JSON must contain an object")
    result["metrics"] = summarize_repository_benchmark_metrics(result)
    result["failure_taxonomy"] = analyze_repository_benchmark_failures(result)
    result["route_impacts"] = analyze_repository_route_impacts(result)
    return result


def _load_promoted_route_families(
    manual_families: list[str] | None,
    benchmark_json_paths: list[str],
    *,
    min_cases: int,
    require_runtime_efficiency: bool,
) -> list[str] | None:
    values: list[str] = [
        family.strip()
        for family in (manual_families or [])
        if family.strip()
    ]
    has_promotion_source = bool(values) or bool(benchmark_json_paths)
    for raw_path in benchmark_json_paths:
        path = Path(raw_path)
        result = _load_existing_result(path)
        values.extend(
            promoted_route_families_from_benchmark(
                result,
                min_cases=min_cases,
                require_runtime_efficiency=require_runtime_efficiency,
            )
        )
    if not has_promotion_source:
        return None
    seen: set[str] = set()
    promoted: list[str] = []
    for value in values:
        if value in seen:
            continue
        promoted.append(value)
        seen.add(value)
    return promoted


def _load_case_ids(
    case_ids: list[str],
    case_ids_file: str,
    case_buckets: list[str],
    reuse_baseline_result: dict | None = None,
) -> list[str]:
    values = [case_id.strip() for case_id in case_ids if case_id.strip()]
    if case_ids_file:
        try:
            lines = Path(case_ids_file).read_text(encoding="utf-8").splitlines()
        except OSError as exc:
            raise SystemExit(f"case ids file read failed: {exc}") from exc
        values.extend(
            line.strip()
            for line in lines
            if line.strip() and not line.lstrip().startswith("#")
        )
    if case_buckets:
        if reuse_baseline_result is None:
            raise SystemExit("--case-bucket requires --reuse-baseline-json")
        taxonomy = analyze_repository_benchmark_failures(reuse_baseline_result)
        targeted = taxonomy.get("targeted_case_ids", {})
        if not isinstance(targeted, dict):
            raise SystemExit("reuse baseline JSON does not contain targeted case buckets")
        for bucket in case_buckets:
            bucket_name = bucket.strip()
            if not bucket_name:
                continue
            if bucket_name not in targeted:
                available = ", ".join(sorted(str(key) for key in targeted))
                raise SystemExit(
                    f"case bucket not found: {bucket_name}; available: {available}"
                )
            bucket_values = targeted.get(bucket_name, [])
            if not isinstance(bucket_values, list):
                raise SystemExit(f"case bucket is not a list: {bucket_name}")
            values.extend(str(value).strip() for value in bucket_values if str(value).strip())
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value in seen:
            continue
        unique.append(value)
        seen.add(value)
    return unique


def _write_outputs(
    result: dict,
    json_output: str,
    md_output: str,
    summary_output: str,
) -> None:
    rendered = render_repository_benchmark_markdown(result)
    if json_output:
        output = Path(json_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
    if md_output:
        output = Path(md_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(rendered, encoding="utf-8")
    if summary_output:
        output = Path(summary_output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            render_repository_benchmark_resume_summary(result),
            encoding="utf-8",
        )


if __name__ == "__main__":
    raise SystemExit(main())

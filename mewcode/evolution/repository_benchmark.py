"""Repository fixture loading for double-run benchmarks."""

from __future__ import annotations

import difflib
import json
import os
import shutil
import stat
import subprocess
import tempfile
import time
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath, PureWindowsPath
from typing import Any, Callable

from mewcode.agent import Agent
from mewcode.client import LLMClient, LLMError
from mewcode.permissions import (
    DangerousCommandDetector,
    PathSandbox,
    PermissionChecker,
    PermissionMode,
    RuleEngine,
)
from mewcode.tools import create_default_registry


ClientFactory = Callable[[Path, bool], LLMClient]

_IGNORED_SNAPSHOT_DIRECTORIES = frozenset(
    {
        ".eggs",
        ".git",
        ".mewcode",
        ".mypy_cache",
        ".pytest_cache",
        "__pycache__",
        "build",
        "dist",
    }
)
_COMMAND_OUTPUT_LIMIT = 10_000


@dataclass(frozen=True)
class RepositoryFixture:
    id: str
    repository: Path
    issue: str
    test_command: str
    regression_command: str
    expected_tests: tuple[str, ...]
    allowed_paths: tuple[str, ...]
    forbidden_paths: tuple[str, ...]


@dataclass(frozen=True)
class RepositoryTaskRoute:
    family: str
    action: str
    skill_name: str
    skill: str
    hint: str
    reason: str

    def to_dict(self) -> dict[str, str]:
        return {
            "family": self.family,
            "action": self.action,
            "skill_name": self.skill_name,
            "hint": self.hint,
            "reason": self.reason,
        }


_ROUTED_REPOSITORY_SKILLS = {
    "requests": (
        "requests_streaming_repair",
        """Requests streaming/utility repair tasks:
- Start from the failing target test and the exact Requests utility/body/header path under test.
- Read only the target test and allowed source files before patching.
- Preserve existing public API behavior and avoid broad networking/session rewrites.
- Write the smallest source patch, run the target test, then run the regression command.
""".strip(),
    ),
    "sympy_printing": (
        "sympy_printing_repair",
        """SymPy printer repair tasks:
- Treat the expected string in the fail-to-pass test as the spec.
- Inspect the target printer class and nearby _print_* methods before editing.
- Prefer a narrow printer dispatch/formatting method in the allowed printer file.
- Do not change global simplification, assumptions, or unrelated expression behavior.
- Run the target printer test first, then the regression command.
""".strip(),
    ),
    "sympy_matrices": (
        "sympy_matrix_repair",
        """SymPy matrix repair tasks:
- Focus on the failing constructor/helper/index invariant named by the test.
- Preserve existing shape behavior, especially zero-dimension and sparse/dense variants.
- Patch the narrow matrix method in allowed paths; avoid broad algebra rewrites.
- Run the target matrix test first, then the regression command.
""".strip(),
    ),
    "sympy_functions": (
        "sympy_function_repair",
        """SymPy function repair tasks:
- Locate the smallest eval/rewrite/printing branch for the failing function.
- Add a narrow guard for the target input rather than changing global simplification.
- Preserve existing regression behavior before considering wider canonicalization changes.
""".strip(),
    ),
    "sympy_integrals": (
        "sympy_integral_repair",
        """SymPy integral repair tasks:
- Trace the failing integration branch with the smallest expression from the test.
- Preserve existing heuristics and add a narrow condition where the wrong branch is selected.
- Run the target integral test and regression command after each meaningful patch.
""".strip(),
    ),
    "sympy_core": (
        "sympy_core_repair",
        """SymPy core repair tasks:
- Reproduce the failing expression and identify where the wrong canonical/eval form is produced.
- Patch the narrow constructor/eval guard; avoid broad arithmetic rewrites.
- Keep the regression command green before stopping.
""".strip(),
    ),
    "flask": (
        "flask_api_repair",
        """Flask behavior repair tasks:
- Locate the public API boundary under test and the narrow validation/normalization point.
- Preserve existing compatibility behavior and avoid changelog/docs/generated-file edits.
- Run target and regression tests after the minimal source patch.
""".strip(),
    ),
    "pytest": (
        "pytest_internal_repair",
        """pytest internals repair tasks:
- Reproduce the assertion/rewrite failure and patch the narrow internal helper under test.
- Preserve existing collection/rewrite semantics and run the provided regression subset.
""".strip(),
    ),
}


def snapshot_repository(root: str | Path) -> dict[str, str]:
    """Read regular repository files into a stable relative-path snapshot."""
    repository_root = Path(root)
    snapshot: dict[str, str] = {}
    for directory, directory_names, file_names in os.walk(repository_root):
        directory_names[:] = sorted(
            name
            for name in directory_names
            if not _is_ignored_snapshot_directory(name)
        )
        current_directory = Path(directory)
        for file_name in sorted(file_names):
            path = current_directory / file_name
            try:
                if not stat.S_ISREG(path.lstat().st_mode):
                    continue
                content = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            relative_path = path.relative_to(repository_root).as_posix()
            snapshot[relative_path] = content
    return dict(sorted(snapshot.items()))


def _is_ignored_snapshot_directory(name: str) -> bool:
    return name in _IGNORED_SNAPSHOT_DIRECTORIES or name.endswith(".egg-info")


def compare_snapshots(
    before: dict[str, str],
    after: dict[str, str],
    allowed_paths: tuple[str, ...] | list[str],
    forbidden_paths: tuple[str, ...] | list[str] = (),
) -> dict[str, Any]:
    """Compare snapshots and report changed paths, scope violations, and line counts."""
    changed_paths = sorted(
        path
        for path in before.keys() | after.keys()
        if before.get(path) != after.get(path)
    )
    out_of_scope_changes = [
        path
        for path in changed_paths
        if not any(fnmatch(path, rule) for rule in allowed_paths)
    ]
    forbidden_changes = [
        path
        for path in changed_paths
        if any(fnmatch(path, rule) for rule in forbidden_paths)
    ]

    added = 0
    removed = 0
    for path in changed_paths:
        diff = difflib.unified_diff(
            before.get(path, "").splitlines(keepends=True),
            after.get(path, "").splitlines(keepends=True),
            fromfile=path,
            tofile=path,
        )
        for index, line in enumerate(diff):
            if index < 2:
                continue
            if line.startswith("+"):
                added += 1
            elif line.startswith("-"):
                removed += 1

    return {
        "changed_paths": changed_paths,
        "out_of_scope_changes": out_of_scope_changes,
        "forbidden_changes": forbidden_changes,
        "patch_size": {
            "added": added,
            "removed": removed,
            "total": added + removed,
        },
    }


def _normalize_command_output(output: str | bytes | None) -> str:
    if output is None:
        return ""
    if isinstance(output, bytes):
        output = output.decode("utf-8", errors="replace")
    return output[-_COMMAND_OUTPUT_LIMIT:]


def run_local_command(
    command: str,
    cwd: str | Path,
    timeout_seconds: float,
) -> dict[str, Any]:
    """Run a benchmark command and normalize its result."""
    started_at = time.perf_counter()
    try:
        completed = subprocess.run(
            command,
            shell=True,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
        status = "passed" if completed.returncode == 0 else "failed"
        exit_code: int | None = completed.returncode
        stdout = completed.stdout
        stderr = completed.stderr
    except subprocess.TimeoutExpired as exc:
        status = "timeout"
        exit_code = None
        stdout = exc.stdout
        stderr = exc.stderr

    return {
        "status": status,
        "exit_code": exit_code,
        "stdout": _normalize_command_output(stdout),
        "stderr": _normalize_command_output(stderr),
        "elapsed_seconds": time.perf_counter() - started_at,
    }


async def run_repository_case(
    fixture: RepositoryFixture,
    repository_root: Path,
    *,
    evolved: bool,
    candidate_skill: str,
    client_factory: ClientFactory,
    protocol: str,
    max_iterations: int,
    test_timeout_seconds: float,
    strategy_hint: str = "",
) -> dict[str, Any]:
    """Run one side of a repository benchmark in its isolated repository."""
    started_at = time.perf_counter()
    before = snapshot_repository(repository_root)
    status = "completed"
    error_type = ""
    error = ""
    final_text = ""
    tool_call_count = 0
    model_call_count = 0
    permission_denied = 0
    rewind_used = False
    agent: Agent | None = None

    try:
        client = client_factory(repository_root, evolved)
        checker = PermissionChecker(
            detector=DangerousCommandDetector(),
            sandbox=PathSandbox(str(repository_root)),
            rule_engine=RuleEngine(),
            mode=PermissionMode.DONT_ASK,
        )
        registry = create_default_registry(work_dir=repository_root)
        agent = Agent(
            client,
            registry,
            protocol,
            work_dir=repository_root,
            max_iterations=max_iterations,
            permission_checker=checker,
        )
        if evolved and candidate_skill.strip():
            agent.activate_skill("candidate", candidate_skill)
    except LLMError as exc:
        status = "provider-failed"
        error_type = type(exc).__name__
        error = str(exc)
    except Exception as exc:
        status = "runner-failed"
        error_type = type(exc).__name__
        error = str(exc)

    if agent is not None and status == "completed":
        original_execute = agent._execute_tool_noninteractive

        async def execute_and_track(tool_call: Any) -> Any:
            nonlocal permission_denied
            result = await original_execute(tool_call)
            if result.is_error and result.output.startswith("Permission denied:"):
                permission_denied += 1
            return result

        agent._execute_tool_noninteractive = execute_and_track  # type: ignore[method-assign]

        def record_event(event: dict[str, Any]) -> None:
            nonlocal model_call_count, tool_call_count, rewind_used
            event_type = event.get("type")
            if event_type == "usage":
                model_call_count += 1
                return
            if event_type != "tool_use":
                return
            tool_call_count += 1
            tool_name = str(event.get("toolName", ""))
            if "rewind" in tool_name.casefold():
                rewind_used = True

        try:
            final_text = await agent.run_to_completion(
                _repository_benchmark_task(fixture, strategy_hint=strategy_hint),
                event_callback=record_event,
            )
        except LLMError as exc:
            status = "provider-failed"
            error_type = type(exc).__name__
            error = str(exc)
        except Exception as exc:
            status = "runner-failed"
            error_type = type(exc).__name__
            error = str(exc)

    regression_result = (
        run_local_command(
            fixture.regression_command,
            repository_root,
            test_timeout_seconds,
        )
        if fixture.regression_command
        else None
    )
    test_result = run_local_command(
        fixture.test_command,
        repository_root,
        test_timeout_seconds,
    )
    after = snapshot_repository(repository_root)
    comparison = compare_snapshots(
        before,
        after,
        fixture.allowed_paths,
        fixture.forbidden_paths,
    )

    tests_passed = test_result["status"] == "passed"
    regression_free = (
        regression_result is None or regression_result["status"] == "passed"
    )
    expected_tests_present = all(
        (repository_root / path).is_file() for path in fixture.expected_tests
    )
    task_success = (
        status == "completed"
        and tests_passed
        and expected_tests_present
        and not comparison["forbidden_changes"]
        and not comparison["out_of_scope_changes"]
    )

    return {
        "status": status,
        "task_success": task_success,
        "tests_passed": tests_passed,
        "regression_free": regression_free,
        "expected_tests_present": expected_tests_present,
        **comparison,
        "input_tokens": agent.total_input_tokens if agent is not None else 0,
        "output_tokens": agent.total_output_tokens if agent is not None else 0,
        "elapsed_seconds": time.perf_counter() - started_at,
        "model_call_count": model_call_count,
        "tool_call_count": tool_call_count,
        "permission_denied": permission_denied,
        "rewind_used": rewind_used,
        "regression_result": regression_result,
        "test_result": test_result,
        "final_text": final_text,
        "error_type": error_type,
        "error": error,
    }


def _repository_benchmark_task(
    fixture: RepositoryFixture,
    *,
    strategy_hint: str = "",
) -> str:
    lines = [
        fixture.issue.strip(),
        "",
        "Repository benchmark constraints:",
        f"- Target test command: `{fixture.test_command}`",
    ]
    if fixture.regression_command:
        lines.append(f"- Regression test command: `{fixture.regression_command}`")
    if fixture.expected_tests:
        lines.append(
            "- Expected test files already exist: "
            + ", ".join(f"`{path}`" for path in fixture.expected_tests)
        )
    if fixture.allowed_paths:
        lines.append(
            "- Only these path patterns are allowed to change: "
            + ", ".join(f"`{path}`" for path in fixture.allowed_paths)
        )
    if fixture.forbidden_paths:
        lines.append(
            "- These path patterns must not change: "
            + ", ".join(f"`{path}`" for path in fixture.forbidden_paths)
        )
    lines.extend([
        "- Treat existing fail-to-pass tests as the spec; do not edit tests unless they are explicitly allowed and necessary.",
        "- Do not modify generated metadata, changelogs, docs, dependency files, or environment shims just to satisfy tests.",
        "- A successful run must pass the target test and regression test without out-of-scope or forbidden changes.",
    ])
    if strategy_hint.strip():
        lines.extend(["", "Strategy router hint:", strategy_hint.strip()])
    return "\n".join(lines)


async def run_repository_double_run_benchmark(
    client_factory: ClientFactory,
    fixture_root: str | Path,
    candidate_skill: str,
    *,
    workspace_root: str | Path | None = None,
    protocol: str = "anthropic",
    max_iterations: int = 20,
    evolved_max_iterations: int | None = None,
    test_timeout_seconds: float = 120.0,
    max_cases: int | None = None,
    reuse_baseline_result: dict[str, Any] | None = None,
    case_ids: tuple[str, ...] | list[str] | None = None,
    strategy_router_enabled: bool = False,
    task_router_enabled: bool = False,
    task_router_short_circuit_skips: bool = True,
    task_router_promoted_families: tuple[str, ...] | list[str] | None = None,
) -> dict[str, Any]:
    """Compare baseline and candidate-skill Agent runs on copied repositories."""
    all_fixtures = load_repository_fixtures(fixture_root)
    fixture_case_count = len(all_fixtures)
    fixtures = _filter_repository_fixtures(all_fixtures, case_ids)
    if max_cases is not None:
        fixtures = fixtures[: max(0, int(max_cases))]
    reusable_baselines = _reusable_baselines_by_case_id(reuse_baseline_result)

    workspace_parent = Path(workspace_root) if workspace_root is not None else None
    if workspace_parent is not None:
        workspace_parent.mkdir(parents=True, exist_ok=True)
    temporary = tempfile.TemporaryDirectory(
        prefix="mewcode-repository-double-run-",
        dir=workspace_parent,
    )
    run_root = Path(temporary.name)
    cases: list[dict[str, Any]] = []
    baseline_runs_executed = 0
    evolved_runs_executed = 0
    reused_baseline_cases = 0
    promoted_route_families = _normalize_route_family_filter(task_router_promoted_families)
    started_at = time.perf_counter()
    try:
        for index, fixture in enumerate(fixtures, 1):
            case_workspace = run_root / f"case_{index:04d}_{fixture.id}"
            case_workspace.mkdir(parents=True, exist_ok=True)
            task_route = (
                _repository_task_route(fixture, promoted_route_families)
                if task_router_enabled
                else None
            )

            baseline = reusable_baselines.get(fixture.id)
            if baseline is None:
                baseline_root = case_workspace / "baseline"
                shutil.copytree(fixture.repository, baseline_root)
                baseline = await run_repository_case(
                    fixture,
                    baseline_root,
                    evolved=False,
                    candidate_skill="",
                    client_factory=client_factory,
                    protocol=protocol,
                    max_iterations=max_iterations,
                    test_timeout_seconds=test_timeout_seconds,
                )
                baseline_runs_executed += 1
            else:
                reused_baseline_cases += 1
            if (
                task_route is not None
                and task_route.action == "skip"
                and task_router_short_circuit_skips
            ):
                evolved = _repository_skipped_evolved_result(baseline, task_route)
            else:
                evolved_root = case_workspace / "evolved"
                shutil.copytree(fixture.repository, evolved_root)
                evolved_candidate_skill = (
                    _compose_repository_routed_skill(candidate_skill, task_route)
                    if task_route is not None and task_route.action == "inject"
                    else candidate_skill
                )
                if task_route is not None and task_route.action == "skip":
                    evolved_candidate_skill = ""
                evolved_strategy_hint = ""
                if task_router_enabled and task_route is not None:
                    evolved_strategy_hint = _repository_route_hint(task_route)
                elif strategy_router_enabled:
                    evolved_strategy_hint = _repository_strategy_hint(fixture)

                evolved = await run_repository_case(
                    fixture,
                    evolved_root,
                    evolved=True,
                    candidate_skill=evolved_candidate_skill,
                    client_factory=client_factory,
                    protocol=protocol,
                    max_iterations=evolved_max_iterations or max_iterations,
                    test_timeout_seconds=test_timeout_seconds,
                    strategy_hint=evolved_strategy_hint,
                )
                evolved_runs_executed += 1
            case_result = {
                "id": fixture.id,
                "source_repository": str(fixture.repository),
                "baseline": baseline,
                "evolved": evolved,
                "delta": _repository_case_delta(baseline, evolved),
            }
            if task_route is not None:
                case_result["task_route"] = task_route.to_dict()
            cases.append(case_result)
    finally:
        temporary.cleanup()

    summary = _repository_benchmark_summary(cases)
    summary["fixture_case_count"] = fixture_case_count
    summary["elapsed_seconds"] = round(time.perf_counter() - started_at, 6)
    summary["baseline_runs_executed"] = baseline_runs_executed
    summary["evolved_runs_executed"] = evolved_runs_executed
    summary["agent_runs_executed"] = baseline_runs_executed + evolved_runs_executed
    summary["reused_baseline_cases"] = reused_baseline_cases
    if task_router_enabled:
        routed_cases = [case.get("task_route", {}) for case in cases]
        summary["task_router_injections"] = sum(
            1 for route in routed_cases if route.get("action") == "inject"
        )
        summary["task_router_skips"] = sum(
            1 for route in routed_cases if route.get("action") == "skip"
        )
        summary["task_router_short_circuits"] = sum(
            1
            for case in cases
            if isinstance(case.get("evolved"), dict)
            and case["evolved"].get("agent_run_skipped")
        )
    metrics = summarize_repository_benchmark_metrics({"summary": summary, "cases": cases})
    failure_taxonomy = analyze_repository_benchmark_failures({"cases": cases})
    route_impacts = analyze_repository_route_impacts({"cases": cases})
    return {
        "method": "repository_double_run",
        "fixture_root": str(fixture_root),
        "configuration": {
            "protocol": protocol,
            "max_iterations": max_iterations,
            "evolved_max_iterations": evolved_max_iterations or max_iterations,
            "test_timeout_seconds": test_timeout_seconds,
            "max_cases": max_cases,
            "case_ids": tuple(case_ids or ()),
            "strategy_router_enabled": strategy_router_enabled,
            "task_router_enabled": task_router_enabled,
            "task_router_short_circuit_skips": task_router_short_circuit_skips,
            "task_router_promoted_families": sorted(promoted_route_families or ()),
            "baseline_reuse_enabled": reuse_baseline_result is not None,
            "reused_baseline_cases": reused_baseline_cases,
            "candidate_skill_injected": (
                bool(summary.get("task_router_injections", 0))
                if task_router_enabled
                else bool(candidate_skill.strip())
            ),
            "task_router_injections": summary.get("task_router_injections", 0),
            "task_router_skips": summary.get("task_router_skips", 0),
            "task_router_short_circuits": summary.get("task_router_short_circuits", 0),
            "automatic_promotion": False,
            "formal_skill_mutation": False,
        },
        "summary": summary,
        "metrics": metrics,
        "failure_taxonomy": failure_taxonomy,
        "route_impacts": route_impacts,
        "cases": cases,
    }


def summarize_repository_benchmark_metrics(result: dict[str, Any]) -> dict[str, Any]:
    """Calculate resume-ready rates and deltas from a double-run result."""
    summary = result.get("summary", {}) if isinstance(result, dict) else {}
    cases = result.get("cases", []) if isinstance(result, dict) else []
    case_count = int(summary.get("case_count", 0) or 0)
    fixture_case_count = int(summary.get("fixture_case_count", case_count) or 0)
    run_count = case_count * 2
    full_run_count = fixture_case_count * 2
    executed_run_count = int(summary.get("agent_runs_executed", run_count) or 0)
    baseline_success = int(summary.get("baseline_success", 0) or 0)
    evolved_success = int(summary.get("evolved_success", 0) or 0)
    evolved_regression_free = int(summary.get("evolved_regression_free", 0) or 0)
    baseline_input_tokens = int(summary.get("baseline_input_tokens", 0) or 0)
    evolved_input_tokens = int(summary.get("evolved_input_tokens", 0) or 0)
    baseline_output_tokens = int(summary.get("baseline_output_tokens", 0) or 0)
    evolved_output_tokens = int(summary.get("evolved_output_tokens", 0) or 0)
    baseline_model_calls = int(summary.get("baseline_model_call_count", 0) or 0)
    evolved_model_calls = int(summary.get("evolved_model_call_count", 0) or 0)
    baseline_tool_calls = int(summary.get("baseline_tool_call_count", 0) or 0)
    evolved_tool_calls = int(summary.get("evolved_tool_call_count", 0) or 0)
    baseline_elapsed = float(summary.get("baseline_average_elapsed_seconds", 0.0) or 0.0)
    evolved_elapsed = float(summary.get("evolved_average_elapsed_seconds", 0.0) or 0.0)

    baseline_success_rate = _rate(baseline_success, case_count)
    evolved_success_rate = _rate(evolved_success, case_count)
    success_lift = round(evolved_success_rate - baseline_success_rate, 2)
    task_success_lift = evolved_success - baseline_success
    provider_failure_rate = _rate(
        int(summary.get("provider_failed", 0) or 0),
        run_count,
    )
    runner_failure_rate = _rate(
        int(summary.get("runner_failed", 0) or 0),
        run_count,
    )
    timeout_rate = _rate(
        int(summary.get("test_timeouts", 0) or 0),
        run_count,
    )
    evolved_regression_free_rate = _rate(evolved_regression_free, case_count)
    baseline_out_of_scope_count = _side_out_of_scope_count(
        summary,
        cases,
        "baseline",
    )
    evolved_out_of_scope_count = _side_out_of_scope_count(
        summary,
        cases,
        "evolved",
    )
    evolved_out_of_scope_rate = _rate(evolved_out_of_scope_count, case_count)
    canary_gate_passed = (
        case_count > 0
        and task_success_lift > 0
        and evolved_regression_free_rate == 100.0
        and evolved_out_of_scope_rate == 0.0
        and provider_failure_rate == 0.0
        and runner_failure_rate == 0.0
        and timeout_rate == 0.0
    )
    total_token_delta = (
        evolved_input_tokens
        + evolved_output_tokens
        - baseline_input_tokens
        - baseline_output_tokens
    )
    model_call_metrics_present = baseline_model_calls > 0 or evolved_model_calls > 0
    runtime_efficiency_gate_passed = (
        canary_gate_passed
        and model_call_metrics_present
        and evolved_model_calls <= baseline_model_calls
        and evolved_tool_calls <= baseline_tool_calls
        and total_token_delta <= 0
        and evolved_elapsed <= baseline_elapsed
    )
    return {
        "case_count": case_count,
        "fixture_case_count": fixture_case_count,
        "run_count": run_count,
        "full_run_count": full_run_count,
        "executed_run_count": executed_run_count,
        "selected_case_reduction_rate": _rate(
            max(0, fixture_case_count - case_count),
            fixture_case_count,
        ),
        "provider_run_reduction_rate": _rate(
            max(0, run_count - executed_run_count),
            run_count,
        ),
        "full_provider_run_reduction_rate": _rate(
            max(0, full_run_count - executed_run_count),
            full_run_count,
        ),
        "baseline_success_rate": baseline_success_rate,
        "evolved_success_rate": evolved_success_rate,
        "success_rate_lift_percentage_points": success_lift,
        "task_success_lift_count": task_success_lift,
        "tests_passed_lift_count": sum(_case_delta_int(case, "tests_passed") for case in cases),
        "evolved_regression_free_rate": evolved_regression_free_rate,
        "out_of_scope_case_rate": _rate(
            int(summary.get("out_of_scope_case_count", 0) or 0),
            case_count,
        ),
        "baseline_out_of_scope_case_rate": _rate(
            baseline_out_of_scope_count,
            case_count,
        ),
        "evolved_out_of_scope_case_rate": evolved_out_of_scope_rate,
        "provider_failure_run_rate": provider_failure_rate,
        "runner_failure_run_rate": runner_failure_rate,
        "test_timeout_run_rate": timeout_rate,
        "canary_gate_passed": canary_gate_passed,
        "canary_gate_reason": _repository_canary_gate_reason(
            task_success_lift=task_success_lift,
            evolved_regression_free_rate=evolved_regression_free_rate,
            evolved_out_of_scope_rate=evolved_out_of_scope_rate,
            provider_failure_rate=provider_failure_rate,
            runner_failure_rate=runner_failure_rate,
            timeout_rate=timeout_rate,
        ),
        "runtime_efficiency_gate_passed": runtime_efficiency_gate_passed,
        "runtime_efficiency_gate_reason": _repository_runtime_efficiency_gate_reason(
            canary_gate_passed=canary_gate_passed,
            model_call_metrics_present=model_call_metrics_present,
            baseline_model_calls=baseline_model_calls,
            evolved_model_calls=evolved_model_calls,
            baseline_tool_calls=baseline_tool_calls,
            evolved_tool_calls=evolved_tool_calls,
            total_token_delta=total_token_delta,
            baseline_elapsed=baseline_elapsed,
            evolved_elapsed=evolved_elapsed,
        ),
        "input_token_delta_total": evolved_input_tokens - baseline_input_tokens,
        "input_token_delta_rate": _relative_delta(
            evolved_input_tokens,
            baseline_input_tokens,
        ),
        "output_token_delta_total": evolved_output_tokens - baseline_output_tokens,
        "output_token_delta_rate": _relative_delta(
            evolved_output_tokens,
            baseline_output_tokens,
        ),
        "total_token_delta_total": total_token_delta,
        "total_token_delta_rate": _relative_delta(
            evolved_input_tokens + evolved_output_tokens,
            baseline_input_tokens + baseline_output_tokens,
        ),
        "model_call_delta_total": evolved_model_calls - baseline_model_calls,
        "model_call_delta_rate": _relative_delta(
            evolved_model_calls,
            baseline_model_calls,
        ),
        "tool_call_delta_total": evolved_tool_calls - baseline_tool_calls,
        "tool_call_delta_rate": _relative_delta(
            evolved_tool_calls,
            baseline_tool_calls,
        ),
        "average_elapsed_delta_seconds": round(evolved_elapsed - baseline_elapsed, 6),
        "average_elapsed_delta_rate": _relative_delta(evolved_elapsed, baseline_elapsed),
        "patch_size_delta_total": sum(
            _case_delta_int(case, "patch_size_total") for case in cases
        ),
        "task_router_injection_count": int(summary.get("task_router_injections", 0) or 0),
        "task_router_skip_count": int(summary.get("task_router_skips", 0) or 0),
        "task_router_short_circuit_count": int(
            summary.get("task_router_short_circuits", 0) or 0
        ),
    }


def analyze_repository_benchmark_failures(result: dict[str, Any]) -> dict[str, Any]:
    """Classify repository benchmark outcomes into actionable failure buckets."""
    cases = result.get("cases", []) if isinstance(result, dict) else []
    if not isinstance(cases, list):
        cases = []

    task_family_counts: dict[str, int] = {}
    outcome_counts: dict[str, int] = {}
    baseline_failure_counts: dict[str, int] = {}
    evolved_failure_counts: dict[str, int] = {}
    targeted_case_ids: dict[str, list[str]] = {
        "regression": [],
        "unsolved": [],
        "evolved_failed": [],
        "evolved_no_patch": [],
        "evolved_patch_failed_tests": [],
    }

    case_rows: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id", "")).strip()
        baseline = case.get("baseline", {}) if isinstance(case.get("baseline"), dict) else {}
        evolved = case.get("evolved", {}) if isinstance(case.get("evolved"), dict) else {}
        family = _repository_task_family(case)
        baseline_failure = _repository_failure_type(baseline)
        evolved_failure = _repository_failure_type(evolved)
        outcome = _repository_outcome_type(baseline, evolved)
        _increment_count(task_family_counts, family)
        _increment_count(outcome_counts, outcome)
        if baseline_failure != "success":
            _increment_count(baseline_failure_counts, baseline_failure)
        if evolved_failure != "success":
            _increment_count(evolved_failure_counts, evolved_failure)
        if case_id:
            if outcome == "regression":
                targeted_case_ids["regression"].append(case_id)
            if outcome == "both_failed":
                targeted_case_ids["unsolved"].append(case_id)
            if evolved_failure != "success":
                targeted_case_ids["evolved_failed"].append(case_id)
            if evolved_failure == "tests_failed_no_patch":
                targeted_case_ids["evolved_no_patch"].append(case_id)
            if evolved_failure == "tests_failed_with_patch":
                targeted_case_ids["evolved_patch_failed_tests"].append(case_id)
        case_rows.append({
            "id": case_id,
            "task_family": family,
            "outcome": outcome,
            "baseline_failure_type": baseline_failure,
            "evolved_failure_type": evolved_failure,
        })

    return {
        "task_family_counts": dict(sorted(task_family_counts.items())),
        "outcome_counts": dict(sorted(outcome_counts.items())),
        "baseline_failure_type_counts": dict(sorted(baseline_failure_counts.items())),
        "evolved_failure_type_counts": dict(sorted(evolved_failure_counts.items())),
        "targeted_case_ids": targeted_case_ids,
        "cases": case_rows,
    }


def analyze_repository_route_impacts(
    result: dict[str, Any],
    *,
    min_cases: int = 1,
    require_runtime_efficiency: bool = False,
) -> dict[str, Any]:
    """Summarize task-router impact by family and recommend promotion gates."""
    cases = result.get("cases", []) if isinstance(result, dict) else []
    if not isinstance(cases, list):
        cases = []

    rows: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        route = case.get("task_route", {}) if isinstance(case.get("task_route"), dict) else {}
        family = str(route.get("family") or _repository_task_family(case)).strip() or "unknown"
        action = str(route.get("action", "")).strip()
        baseline = case.get("baseline", {}) if isinstance(case.get("baseline"), dict) else {}
        evolved = case.get("evolved", {}) if isinstance(case.get("evolved"), dict) else {}
        delta = case.get("delta", {}) if isinstance(case.get("delta"), dict) else {}
        row = rows.setdefault(family, _empty_route_impact_row(family))
        row["case_count"] += 1
        row["case_ids"].append(str(case.get("id", "")).strip())
        if action == "inject":
            row["injected_case_count"] += 1
        elif action == "skip":
            row["skipped_case_count"] += 1
        if evolved.get("agent_run_skipped"):
            row["short_circuit_count"] += 1

        baseline_success = bool(baseline.get("task_success"))
        evolved_success = bool(evolved.get("task_success"))
        row["baseline_success"] += _bool_int(baseline_success)
        row["evolved_success"] += _bool_int(evolved_success)
        if not baseline_success and evolved_success:
            row["lift_count"] += 1
        if baseline_success and not evolved_success:
            row["regression_count"] += 1
        if baseline_success and evolved_success:
            row["both_success_count"] += 1
        if not baseline_success and not evolved_success:
            row["both_failed_count"] += 1
        if not bool(evolved.get("regression_free", True)):
            row["evolved_regression_failed_count"] += 1
        if evolved.get("out_of_scope_changes"):
            row["evolved_out_of_scope_count"] += 1
        if evolved.get("status") in {"provider-failed", "runner-failed"}:
            row["evolved_execution_failure_count"] += 1
        if _has_test_timeout(evolved):
            row["evolved_timeout_count"] += 1

        row["task_success_lift_count"] += int(delta.get("task_success", 0) or 0)
        row["model_call_delta_total"] += int(delta.get("model_call_count", 0) or 0)
        row["tool_call_delta_total"] += int(delta.get("tool_call_count", 0) or 0)
        row["input_token_delta_total"] += int(delta.get("input_tokens", 0) or 0)
        row["output_token_delta_total"] += int(delta.get("output_tokens", 0) or 0)
        row["elapsed_delta_total"] = round(
            float(row["elapsed_delta_total"])
            + float(delta.get("elapsed_seconds", 0.0) or 0.0),
            6,
        )
        row["patch_size_delta_total"] += int(delta.get("patch_size_total", 0) or 0)

    family_rows: list[dict[str, Any]] = []
    promoted: list[str] = []
    for family in sorted(rows):
        row = rows[family]
        row["baseline_success_rate"] = _rate(row["baseline_success"], row["case_count"])
        row["evolved_success_rate"] = _rate(row["evolved_success"], row["case_count"])
        row["success_rate_lift_percentage_points"] = round(
            row["evolved_success_rate"] - row["baseline_success_rate"],
            2,
        )
        row["total_token_delta_total"] = (
            int(row["input_token_delta_total"])
            + int(row["output_token_delta_total"])
        )
        promotable, reason = _repository_route_promotion_decision(
            row,
            min_cases=max(1, int(min_cases or 1)),
            require_runtime_efficiency=require_runtime_efficiency,
        )
        row["promotion_recommended"] = promotable
        row["promotion_reason"] = reason
        if promotable:
            promoted.append(family)
        family_rows.append(row)

    return {
        "criteria": {
            "min_cases": max(1, int(min_cases or 1)),
            "require_runtime_efficiency": bool(require_runtime_efficiency),
        },
        "promoted_families": promoted,
        "families": family_rows,
    }


def promoted_route_families_from_benchmark(
    result: dict[str, Any],
    *,
    min_cases: int = 1,
    require_runtime_efficiency: bool = False,
) -> list[str]:
    """Return route families that passed the benchmark promotion gate."""
    impacts = analyze_repository_route_impacts(
        result,
        min_cases=min_cases,
        require_runtime_efficiency=require_runtime_efficiency,
    )
    promoted = impacts.get("promoted_families", [])
    if not isinstance(promoted, list):
        return []
    return [str(family) for family in promoted if str(family).strip()]


def recompute_repository_task_router_policy(
    result: dict[str, Any],
    *,
    promoted_families: tuple[str, ...] | list[str] | None = None,
    min_cases: int = 1,
    require_runtime_efficiency: bool = False,
    short_circuit_skips: bool = True,
) -> dict[str, Any]:
    """Recompute a task-router promotion policy from existing benchmark results."""
    source = json.loads(json.dumps(result))
    promoted = (
        sorted(_normalize_route_family_filter(promoted_families) or ())
        if promoted_families is not None
        else promoted_route_families_from_benchmark(
            source,
            min_cases=min_cases,
            require_runtime_efficiency=require_runtime_efficiency,
        )
    )
    promoted_set = set(promoted)
    cases: list[dict[str, Any]] = []
    injected_count = 0
    skipped_count = 0
    short_circuit_count = 0

    for raw_case in source.get("cases", []) or []:
        if not isinstance(raw_case, dict):
            continue
        case = json.loads(json.dumps(raw_case))
        route = case.get("task_route", {}) if isinstance(case.get("task_route"), dict) else {}
        family = str(route.get("family", "")).strip()
        action = str(route.get("action", "")).strip()
        should_inject = bool(route) and action == "inject" and family in promoted_set
        if should_inject:
            injected_count += 1
            route["reason"] = _append_route_reason(
                route.get("reason", ""),
                "family skill passed route promotion gate",
            )
            case["task_route"] = route
            case["delta"] = _repository_case_delta(
                case.get("baseline", {}),
                case.get("evolved", {}),
            )
            cases.append(case)
            continue

        if route and short_circuit_skips:
            skipped_count += 1
            short_circuit_count += 1
            if action == "inject":
                skill_name = str(route.get("skill_name", ""))
                route["action"] = "skip"
                route["hint"] = (
                    f"Task router matched `{skill_name}` for family `{family}`, but this "
                    "family Skill did not pass the promoted route policy. Do not inject "
                    "the candidate Skill; rely on the baseline repository repair prompt."
                )
                route["reason"] = "routed family skill not promoted by policy recompute"
            else:
                route["action"] = "skip"
            case["task_route"] = route
            case["evolved"] = _repository_skipped_evolved_result(
                case.get("baseline", {}),
                RepositoryTaskRoute(
                    family=family or "unknown",
                    action="skip",
                    skill_name=str(route.get("skill_name", "")),
                    skill="",
                    hint=str(route.get("hint", "")),
                    reason=str(route.get("reason", "")),
                ),
            )
            case["delta"] = _repository_case_delta(
                case.get("baseline", {}),
                case.get("evolved", {}),
            )
        cases.append(case)

    summary = _repository_benchmark_summary(cases)
    source_summary = source.get("summary", {}) if isinstance(source.get("summary"), dict) else {}
    summary["fixture_case_count"] = int(
        source_summary.get("fixture_case_count", summary.get("case_count", 0)) or 0
    )
    summary["elapsed_seconds"] = source_summary.get("elapsed_seconds", 0)
    summary["baseline_runs_executed"] = 0
    summary["evolved_runs_executed"] = injected_count
    summary["agent_runs_executed"] = injected_count
    summary["reused_baseline_cases"] = summary.get("case_count", 0)
    summary["task_router_injections"] = injected_count
    summary["task_router_skips"] = skipped_count
    summary["task_router_short_circuits"] = short_circuit_count
    source_agent_runs = int(source_summary.get("agent_runs_executed", 0) or 0)
    summary["policy_recompute_source_agent_runs"] = source_agent_runs

    configuration = source.get("configuration", {}) if isinstance(source.get("configuration"), dict) else {}
    configuration = json.loads(json.dumps(configuration))
    configuration.update({
        "policy_recompute": True,
        "policy_recompute_source_method": source.get("method", ""),
        "policy_recompute_note": (
            "Offline recompute: promoted route families retain source evolved results; "
            "other routed cases reuse baseline policy. No provider calls were executed."
        ),
        "task_router_enabled": True,
        "task_router_short_circuit_skips": short_circuit_skips,
        "task_router_promoted_families": promoted,
        "task_router_promotion_min_cases": max(1, int(min_cases or 1)),
        "task_router_promotion_require_efficiency": bool(require_runtime_efficiency),
        "candidate_skill_injected": injected_count > 0,
        "task_router_injections": injected_count,
        "task_router_skips": skipped_count,
        "task_router_short_circuits": short_circuit_count,
        "automatic_promotion": False,
        "formal_skill_mutation": False,
    })
    recomputed = {
        "method": "repository_task_router_policy_recompute",
        "fixture_root": source.get("fixture_root", ""),
        "configuration": configuration,
        "summary": summary,
        "cases": cases,
    }
    recomputed["metrics"] = summarize_repository_benchmark_metrics(recomputed)
    recomputed["failure_taxonomy"] = analyze_repository_benchmark_failures(recomputed)
    recomputed["route_impacts"] = analyze_repository_route_impacts(
        recomputed,
        min_cases=min_cases,
        require_runtime_efficiency=require_runtime_efficiency,
    )
    return recomputed


def _append_route_reason(reason: Any, suffix: str) -> str:
    existing = str(reason).strip()
    if not existing:
        return suffix
    if suffix in existing:
        return existing
    return f"{existing}; {suffix}"


def _empty_route_impact_row(family: str) -> dict[str, Any]:
    return {
        "family": family,
        "case_count": 0,
        "injected_case_count": 0,
        "skipped_case_count": 0,
        "short_circuit_count": 0,
        "baseline_success": 0,
        "evolved_success": 0,
        "lift_count": 0,
        "regression_count": 0,
        "both_success_count": 0,
        "both_failed_count": 0,
        "evolved_regression_failed_count": 0,
        "evolved_out_of_scope_count": 0,
        "evolved_execution_failure_count": 0,
        "evolved_timeout_count": 0,
        "task_success_lift_count": 0,
        "model_call_delta_total": 0,
        "tool_call_delta_total": 0,
        "input_token_delta_total": 0,
        "output_token_delta_total": 0,
        "elapsed_delta_total": 0.0,
        "patch_size_delta_total": 0,
        "case_ids": [],
    }


def _repository_route_promotion_decision(
    row: dict[str, Any],
    *,
    min_cases: int,
    require_runtime_efficiency: bool,
) -> tuple[bool, str]:
    failures: list[str] = []
    if int(row.get("injected_case_count", 0) or 0) < min_cases:
        failures.append("not enough injected cases")
    if int(row.get("task_success_lift_count", 0) or 0) <= 0:
        failures.append("no positive task-success lift")
    if int(row.get("regression_count", 0) or 0) > 0:
        failures.append("route caused task regression")
    if int(row.get("evolved_regression_failed_count", 0) or 0) > 0:
        failures.append("evolved regression tests failed")
    if int(row.get("evolved_out_of_scope_count", 0) or 0) > 0:
        failures.append("evolved out-of-scope changes present")
    if int(row.get("evolved_execution_failure_count", 0) or 0) > 0:
        failures.append("provider or runner failures present")
    if int(row.get("evolved_timeout_count", 0) or 0) > 0:
        failures.append("test timeouts present")
    if require_runtime_efficiency:
        if int(row.get("model_call_delta_total", 0) or 0) > 0:
            failures.append("model calls increased")
        if int(row.get("tool_call_delta_total", 0) or 0) > 0:
            failures.append("tool calls increased")
        if int(row.get("total_token_delta_total", 0) or 0) > 0:
            failures.append("total tokens increased")
        if float(row.get("elapsed_delta_total", 0.0) or 0.0) > 0.0:
            failures.append("elapsed time increased")
    if failures:
        return False, "; ".join(failures)
    return True, "positive lift with clean routed family runs"


def render_repository_benchmark_resume_summary(result: dict[str, Any]) -> str:
    """Render a concise, citation-ready benchmark impact summary."""
    metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
    if not metrics:
        metrics = summarize_repository_benchmark_metrics(result)
    case_count = int(metrics.get("case_count", 0) or 0)
    run_count = int(metrics.get("run_count", 0) or 0)
    lines = [
        "Repository double-run benchmark impact summary:",
        f"- Evaluated {case_count} repository task(s) across {run_count} isolated agent run(s).",
        "- Task success improved from "
        f"{_format_rate(metrics.get('baseline_success_rate'))} to "
        f"{_format_rate(metrics.get('evolved_success_rate'))} "
        f"({_format_signed(metrics.get('success_rate_lift_percentage_points'))} pp, "
        f"{_format_signed(metrics.get('task_success_lift_count'), decimals=0)} case(s)).",
        "- Evolved regression-free rate: "
        f"{_format_rate(metrics.get('evolved_regression_free_rate'))}; "
        "evolved out-of-scope case rate: "
        f"{_format_rate(metrics.get('evolved_out_of_scope_case_rate'))}.",
        "- Canary gate: "
        f"{_pass_fail(metrics.get('canary_gate_passed'))} "
        f"({metrics.get('canary_gate_reason', '')}).",
        "- Runtime efficiency gate: "
        f"{_pass_fail(metrics.get('runtime_efficiency_gate_passed'))} "
        f"({metrics.get('runtime_efficiency_gate_reason', '')}).",
        "- Failure rates across all runs: provider "
        f"{_format_rate(metrics.get('provider_failure_run_rate'))}, runner "
        f"{_format_rate(metrics.get('runner_failure_run_rate'))}, test timeout "
        f"{_format_rate(metrics.get('test_timeout_run_rate'))}.",
        "- Cost/time deltas: "
        f"{_format_signed(metrics.get('model_call_delta_total'), decimals=0)} model calls, "
        f"{_format_signed(metrics.get('tool_call_delta_total'), decimals=0)} tool calls, "
        f"{_format_signed(metrics.get('input_token_delta_total'), decimals=0)} input tokens, "
        f"{_format_signed(metrics.get('output_token_delta_total'), decimals=0)} output tokens, "
        f"{_format_signed(metrics.get('average_elapsed_delta_seconds'), suffix='s')} average elapsed, "
        f"{_format_signed(metrics.get('patch_size_delta_total'), decimals=0)} patch lines.",
        "- Use as an effect metric only for real-provider benchmark JSON; fake-client runs validate the harness only.",
    ]
    short_circuits = int(metrics.get("task_router_short_circuit_count", 0) or 0)
    if short_circuits:
        lines.insert(
            2,
            f"- Task router short-circuited {short_circuits} no-skill skip case(s) to baseline policy.",
        )
    executed_run_count = int(metrics.get("executed_run_count", run_count) or 0)
    if executed_run_count < run_count:
        lines.insert(
            1,
            "- Baseline reuse executed "
            f"{executed_run_count} of {run_count} comparable agent run(s), "
            f"saving {_format_rate(metrics.get('provider_run_reduction_rate'))} provider runs.",
        )
    full_run_count = int(metrics.get("full_run_count", run_count) or 0)
    if full_run_count and executed_run_count < full_run_count:
        lines.insert(
            1,
            "- Targeted evaluation executed "
            f"{executed_run_count} of {full_run_count} full-fixture comparable run(s), "
            f"saving {_format_rate(metrics.get('full_provider_run_reduction_rate'))} provider runs.",
        )
    return "\n".join(lines) + "\n"


def render_repository_benchmark_markdown(result: dict[str, Any]) -> str:
    """Render a compact side-by-side Markdown report for repository benchmarks."""
    summary = result.get("summary", {}) if isinstance(result, dict) else {}
    metrics = result.get("metrics", {}) if isinstance(result, dict) else {}
    if not metrics:
        metrics = summarize_repository_benchmark_metrics(result)
    configuration = result.get("configuration", {}) if isinstance(result, dict) else {}
    cases = result.get("cases", []) if isinstance(result, dict) else []
    failure_taxonomy = result.get("failure_taxonomy", {}) if isinstance(result, dict) else {}
    if not failure_taxonomy:
        failure_taxonomy = analyze_repository_benchmark_failures(result)
    route_impacts = result.get("route_impacts", {}) if isinstance(result, dict) else {}
    if not route_impacts:
        route_impacts = analyze_repository_route_impacts(result)
    lines = [
        "# Repository Double-Run Benchmark",
        "",
        "## Summary",
        "",
        f"- Method: `{result.get('method', 'repository_double_run')}`",
        f"- Fixture root: `{result.get('fixture_root', '')}`",
        f"- Cases: `{summary.get('case_count', 0)}`",
        f"- Baseline success: `{summary.get('baseline_success', 0)}`",
        f"- Evolved success: `{summary.get('evolved_success', 0)}`",
        f"- Provider failures: `{summary.get('provider_failed', 0)}`",
        f"- Runner failures: `{summary.get('runner_failed', 0)}`",
        f"- Test timeouts: `{summary.get('test_timeouts', 0)}`",
        f"- Out-of-scope cases: `{summary.get('out_of_scope_case_count', 0)}`",
        "",
        "## Quantitative Metrics",
        "",
        f"- Baseline success rate: `{_format_rate(metrics.get('baseline_success_rate'))}`",
        f"- Evolved success rate: `{_format_rate(metrics.get('evolved_success_rate'))}`",
        f"- Success lift: `{_format_signed(metrics.get('success_rate_lift_percentage_points'))} pp` / `{_format_signed(metrics.get('task_success_lift_count'), decimals=0)} cases`",
        f"- Evolved regression-free rate: `{_format_rate(metrics.get('evolved_regression_free_rate'))}`",
        f"- Evolved out-of-scope case rate: `{_format_rate(metrics.get('evolved_out_of_scope_case_rate'))}`",
        f"- Canary gate: `{_pass_fail(metrics.get('canary_gate_passed'))}` ({metrics.get('canary_gate_reason', '')})",
        f"- Runtime efficiency gate: `{_pass_fail(metrics.get('runtime_efficiency_gate_passed'))}` ({metrics.get('runtime_efficiency_gate_reason', '')})",
        f"- Selected cases: `{metrics.get('case_count', 0)}` / `{metrics.get('fixture_case_count', metrics.get('case_count', 0))}`",
        f"- Case selection reduction: `{_format_rate(metrics.get('selected_case_reduction_rate'))}`",
        f"- Executed agent runs: `{metrics.get('executed_run_count', metrics.get('run_count', 0))}` / `{metrics.get('run_count', 0)}`",
        f"- Provider run reduction: `{_format_rate(metrics.get('provider_run_reduction_rate'))}`",
        f"- Full-fixture provider run reduction: `{_format_rate(metrics.get('full_provider_run_reduction_rate'))}`",
        f"- Task-router injections: `{metrics.get('task_router_injection_count', 0)}`",
        f"- Task-router skips: `{metrics.get('task_router_skip_count', 0)}`",
        f"- Task-router short-circuits: `{metrics.get('task_router_short_circuit_count', 0)}`",
        f"- Provider failure run rate: `{_format_rate(metrics.get('provider_failure_run_rate'))}`",
        f"- Avg elapsed delta: `{_format_signed(metrics.get('average_elapsed_delta_seconds'), suffix='s')}`",
        f"- Model-call delta: `{_format_signed(metrics.get('model_call_delta_total'), decimals=0)}` (`{_format_signed(metrics.get('model_call_delta_rate'))}%`)",
        f"- Tool-call delta: `{_format_signed(metrics.get('tool_call_delta_total'), decimals=0)}` (`{_format_signed(metrics.get('tool_call_delta_rate'))}%`)",
        f"- Token deltas: `{_format_signed(metrics.get('input_token_delta_total'), decimals=0)} input / {_format_signed(metrics.get('output_token_delta_total'), decimals=0)} output`",
        f"- Total-token delta: `{_format_signed(metrics.get('total_token_delta_total'), decimals=0)}` (`{_format_signed(metrics.get('total_token_delta_rate'))}%`)",
        f"- Patch-size delta: `{_format_signed(metrics.get('patch_size_delta_total'), decimals=0)} lines`",
        "",
        "## Configuration",
        "",
    ]
    for key in sorted(configuration):
        lines.append(f"- {key}: {_markdown_scalar(configuration[key])}")
    if any(isinstance(item, dict) and item.get("task_route") for item in cases):
        lines.extend([
            "",
            "## Task Routes",
            "",
            "| Case | Family | Action | Skill | Reason |",
            "|---|---|---|---|---|",
        ])
        for item in cases:
            route = item.get("task_route", {}) if isinstance(item, dict) else {}
            if not isinstance(route, dict) or not route:
                continue
            lines.append(
                "| "
                f"{item.get('id', '')} | "
                f"{route.get('family', '')} | "
                f"{route.get('action', '')} | "
                f"{route.get('skill_name', '') or '-'} | "
                f"{route.get('reason', '')} |"
            )
    lines.extend(_render_route_impacts_markdown(route_impacts))
    lines.extend([
        "",
        "## Baseline vs Evolved",
        "",
        "| Case | Baseline | Evolved | Delta | Patch Delta | Provider/Runner Failure |",
        "|---|---:|---:|---:|---:|---|",
    ])
    for item in cases:
        baseline = item.get("baseline", {}) if isinstance(item, dict) else {}
        evolved = item.get("evolved", {}) if isinstance(item, dict) else {}
        delta = item.get("delta", {}) if isinstance(item, dict) else {}
        failure = ", ".join(
            part
            for part in (
                _failure_label("baseline", baseline),
                _failure_label("evolved", evolved),
            )
            if part
        ) or "-"
        lines.append(
            "| "
            f"{item.get('id', '')} | "
            f"{_yes_no(baseline.get('task_success'))} | "
            f"{_yes_no(evolved.get('task_success'))} | "
            f"{delta.get('task_success', 0)} | "
            f"{delta.get('patch_size_total', 0)} | "
            f"{failure} |"
        )

    lines.extend(_render_failure_taxonomy_markdown(failure_taxonomy))

    lines.extend(["", "## Case Details", ""])
    for item in cases:
        baseline = item.get("baseline", {}) if isinstance(item, dict) else {}
        evolved = item.get("evolved", {}) if isinstance(item, dict) else {}
        lines.extend([
            f"### {item.get('id', '')}",
            "",
            _render_case_side("Baseline", baseline),
            "",
            _render_case_side("Evolved", evolved),
            "",
        ])

    lines.extend([
        "## Limits",
        "",
        "- This benchmark only compares isolated repository runs; it does not approve, promote, or write formal project skills.",
        "- `evolved_success` measures this runner's task-success predicate, not user approval or production safety by itself.",
        "- Candidate skills must still pass the normal self-evolution gates before becoming long-lived behavior.",
        "",
    ])
    return "\n".join(lines)


def _repository_case_delta(baseline: dict[str, Any], evolved: dict[str, Any]) -> dict[str, Any]:
    return {
        "tests_passed": _bool_int(evolved.get("tests_passed"))
        - _bool_int(baseline.get("tests_passed")),
        "task_success": _bool_int(evolved.get("task_success"))
        - _bool_int(baseline.get("task_success")),
        "regression_free": _bool_int(evolved.get("regression_free"))
        - _bool_int(baseline.get("regression_free")),
        "patch_size_total": _patch_total(evolved) - _patch_total(baseline),
        "elapsed_seconds": round(
            float(evolved.get("elapsed_seconds", 0.0) or 0.0)
            - float(baseline.get("elapsed_seconds", 0.0) or 0.0),
            6,
        ),
        "input_tokens": int(evolved.get("input_tokens", 0) or 0)
        - int(baseline.get("input_tokens", 0) or 0),
        "output_tokens": int(evolved.get("output_tokens", 0) or 0)
        - int(baseline.get("output_tokens", 0) or 0),
        "model_call_count": int(evolved.get("model_call_count", 0) or 0)
        - int(baseline.get("model_call_count", 0) or 0),
        "tool_call_count": int(evolved.get("tool_call_count", 0) or 0)
        - int(baseline.get("tool_call_count", 0) or 0),
    }


def _repository_benchmark_summary(cases: list[dict[str, Any]]) -> dict[str, Any]:
    baseline_items = [case.get("baseline", {}) for case in cases]
    evolved_items = [case.get("evolved", {}) for case in cases]
    all_items = baseline_items + evolved_items
    return {
        "case_count": len(cases),
        "baseline_success": sum(_bool_int(item.get("task_success")) for item in baseline_items),
        "evolved_success": sum(_bool_int(item.get("task_success")) for item in evolved_items),
        "evolved_regression_free": sum(_bool_int(item.get("regression_free")) for item in evolved_items),
        "provider_failed": sum(1 for item in all_items if item.get("status") == "provider-failed"),
        "runner_failed": sum(1 for item in all_items if item.get("status") == "runner-failed"),
        "test_timeouts": sum(1 for item in all_items if _has_test_timeout(item)),
        "out_of_scope_case_count": sum(
            1
            for case in cases
            if case.get("baseline", {}).get("out_of_scope_changes")
            or case.get("evolved", {}).get("out_of_scope_changes")
        ),
        "baseline_out_of_scope_case_count": sum(
            1 for item in baseline_items if item.get("out_of_scope_changes")
        ),
        "evolved_out_of_scope_case_count": sum(
            1 for item in evolved_items if item.get("out_of_scope_changes")
        ),
        "baseline_input_tokens": sum(int(item.get("input_tokens", 0) or 0) for item in baseline_items),
        "baseline_output_tokens": sum(int(item.get("output_tokens", 0) or 0) for item in baseline_items),
        "evolved_input_tokens": sum(int(item.get("input_tokens", 0) or 0) for item in evolved_items),
        "evolved_output_tokens": sum(int(item.get("output_tokens", 0) or 0) for item in evolved_items),
        "baseline_model_call_count": sum(int(item.get("model_call_count", 0) or 0) for item in baseline_items),
        "evolved_model_call_count": sum(int(item.get("model_call_count", 0) or 0) for item in evolved_items),
        "baseline_tool_call_count": sum(int(item.get("tool_call_count", 0) or 0) for item in baseline_items),
        "evolved_tool_call_count": sum(int(item.get("tool_call_count", 0) or 0) for item in evolved_items),
        "baseline_average_elapsed_seconds": _average_elapsed(baseline_items),
        "evolved_average_elapsed_seconds": _average_elapsed(evolved_items),
    }


def _rate(numerator: int | float, denominator: int | float) -> float:
    if not denominator:
        return 0.0
    return round((float(numerator) / float(denominator)) * 100.0, 2)


def _relative_delta(current: int | float, baseline: int | float) -> float:
    if not baseline:
        return 0.0
    return round(((float(current) - float(baseline)) / float(baseline)) * 100.0, 2)


def _case_delta_int(case: Any, field: str) -> int:
    if not isinstance(case, dict):
        return 0
    delta = case.get("delta", {})
    if not isinstance(delta, dict):
        return 0
    return int(delta.get(field, 0) or 0)


def _side_out_of_scope_count(
    summary: dict[str, Any],
    cases: Any,
    side: str,
) -> int:
    summary_key = f"{side}_out_of_scope_case_count"
    if summary_key in summary:
        return int(summary.get(summary_key, 0) or 0)
    if not isinstance(cases, list):
        return 0
    return sum(
        1
        for case in cases
        if isinstance(case, dict)
        and isinstance(case.get(side), dict)
        and case[side].get("out_of_scope_changes")
    )


def _reusable_baselines_by_case_id(
    result: dict[str, Any] | None,
) -> dict[str, dict[str, Any]]:
    if not result:
        return {}
    cases = result.get("cases", [])
    if not isinstance(cases, list):
        return {}
    baselines: dict[str, dict[str, Any]] = {}
    for case in cases:
        if not isinstance(case, dict):
            continue
        case_id = str(case.get("id", "")).strip()
        baseline = case.get("baseline")
        if case_id and isinstance(baseline, dict):
            baselines[case_id] = json.loads(json.dumps(baseline))
    return baselines


def _repository_skipped_evolved_result(
    baseline: dict[str, Any],
    route: RepositoryTaskRoute,
) -> dict[str, Any]:
    """Represent a routed no-op as the baseline policy without rerunning an agent."""
    skipped = json.loads(json.dumps(baseline))
    skipped["status"] = "skipped"
    skipped["agent_run_skipped"] = True
    skipped["candidate_skill_applied"] = False
    skipped["selected_policy"] = "baseline"
    skipped["skip_reason"] = route.hint or route.reason
    skipped["baseline_status"] = baseline.get("status", "")
    skipped["baseline_error_type"] = baseline.get("error_type", "")
    skipped["baseline_error"] = baseline.get("error", "")
    skipped["error_type"] = ""
    skipped["error"] = ""
    return skipped


def _filter_repository_fixtures(
    fixtures: list[RepositoryFixture],
    case_ids: tuple[str, ...] | list[str] | None,
) -> list[RepositoryFixture]:
    requested = tuple(str(case_id).strip() for case_id in (case_ids or ()) if str(case_id).strip())
    if not requested:
        return fixtures
    by_id = {fixture.id: fixture for fixture in fixtures}
    missing = sorted(case_id for case_id in requested if case_id not in by_id)
    if missing:
        raise ValueError("case id(s) not found: " + ", ".join(missing))
    seen: set[str] = set()
    selected: list[RepositoryFixture] = []
    for case_id in requested:
        if case_id in seen:
            continue
        selected.append(by_id[case_id])
        seen.add(case_id)
    return selected


def _bool_int(value: Any) -> int:
    return 1 if bool(value) else 0


def _patch_total(item: dict[str, Any]) -> int:
    patch = item.get("patch_size", {})
    return int(patch.get("total", 0) or 0) if isinstance(patch, dict) else 0


def _has_test_timeout(item: dict[str, Any]) -> bool:
    test_result = item.get("test_result", {})
    regression_result = item.get("regression_result", {})
    return (
        isinstance(test_result, dict)
        and test_result.get("status") == "timeout"
    ) or (
        isinstance(regression_result, dict)
        and regression_result.get("status") == "timeout"
    )


def _average_elapsed(items: list[dict[str, Any]]) -> float:
    if not items:
        return 0.0
    total = sum(float(item.get("elapsed_seconds", 0.0) or 0.0) for item in items)
    return round(total / len(items), 6)


def _markdown_scalar(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    return f"`{value}`"


def _format_rate(value: Any) -> str:
    try:
        return f"{float(value):.2f}%"
    except (TypeError, ValueError):
        return "0.00%"


def _format_signed(
    value: Any,
    *,
    decimals: int = 2,
    suffix: str = "",
) -> str:
    try:
        number = float(value)
    except (TypeError, ValueError):
        number = 0.0
    sign = "+" if number > 0 else ""
    if decimals <= 0:
        return f"{sign}{int(round(number))}{suffix}"
    return f"{sign}{number:.{decimals}f}{suffix}"


def _pass_fail(value: Any) -> str:
    return "pass" if bool(value) else "fail"


def _repository_canary_gate_reason(
    *,
    task_success_lift: int,
    evolved_regression_free_rate: float,
    evolved_out_of_scope_rate: float,
    provider_failure_rate: float,
    runner_failure_rate: float,
    timeout_rate: float,
) -> str:
    failures: list[str] = []
    if task_success_lift <= 0:
        failures.append("no positive task-success lift")
    if evolved_regression_free_rate < 100.0:
        failures.append("evolved regression-free rate below 100%")
    if evolved_out_of_scope_rate > 0.0:
        failures.append("evolved out-of-scope changes present")
    if provider_failure_rate > 0.0:
        failures.append("provider failures present")
    if runner_failure_rate > 0.0:
        failures.append("runner failures present")
    if timeout_rate > 0.0:
        failures.append("test timeouts present")
    if not failures:
        return "positive lift with clean evolved runs"
    return "; ".join(failures)


def _repository_runtime_efficiency_gate_reason(
    *,
    canary_gate_passed: bool,
    model_call_metrics_present: bool,
    baseline_model_calls: int,
    evolved_model_calls: int,
    baseline_tool_calls: int,
    evolved_tool_calls: int,
    total_token_delta: int,
    baseline_elapsed: float,
    evolved_elapsed: float,
) -> str:
    failures: list[str] = []
    if not canary_gate_passed:
        failures.append("canary gate did not pass")
    if not model_call_metrics_present:
        failures.append("model-call metrics missing")
    if evolved_model_calls > baseline_model_calls:
        failures.append("model calls increased")
    if evolved_tool_calls > baseline_tool_calls:
        failures.append("tool calls increased")
    if total_token_delta > 0:
        failures.append("total tokens increased")
    if evolved_elapsed > baseline_elapsed:
        failures.append("average elapsed increased")
    if not failures:
        return "success/safety improved with non-increasing calls, tokens, and elapsed time"
    return "; ".join(failures)


def _repository_failure_type(item: dict[str, Any]) -> str:
    if bool(item.get("task_success")):
        return "success"
    status = str(item.get("status", "")).strip()
    if status == "skipped":
        return "skipped_no_candidate"
    if status == "provider-failed":
        return "provider_failed"
    if status == "runner-failed":
        return "runner_failed"
    if _has_test_timeout(item):
        return "test_timeout"
    if item.get("forbidden_changes"):
        return "forbidden_change"
    if item.get("out_of_scope_changes"):
        return "out_of_scope_change"
    if not bool(item.get("expected_tests_present", True)):
        return "expected_tests_missing"
    if not bool(item.get("regression_free", True)):
        return "regression_failed"
    if not bool(item.get("tests_passed")):
        changed_paths = item.get("changed_paths", [])
        return "tests_failed_with_patch" if changed_paths else "tests_failed_no_patch"
    return "unknown_failure"


def _repository_outcome_type(
    baseline: dict[str, Any],
    evolved: dict[str, Any],
) -> str:
    baseline_success = bool(baseline.get("task_success"))
    evolved_success = bool(evolved.get("task_success"))
    if baseline_success and evolved_success:
        return "both_success"
    if not baseline_success and evolved_success:
        return "lift"
    if baseline_success and not evolved_success:
        return "regression"
    return "both_failed"


def _repository_task_family(case: dict[str, Any]) -> str:
    signal = _repository_case_signal(case)
    if "flask" in signal:
        return "flask"
    if "requests" in signal:
        return "requests"
    if "sympy/printing" in signal or any(
        token in signal
        for token in ("latex", "pretty", "ccode", "mathematica", "printer")
    ):
        return "sympy_printing"
    if "sympy/matrices" in signal or "matrix" in signal:
        return "sympy_matrices"
    if "sympy/functions" in signal:
        return "sympy_functions"
    if "sympy/integrals" in signal:
        return "sympy_integrals"
    if "sympy/core" in signal or any(token in signal for token in ("evalf", "test_arit")):
        return "sympy_core"
    if "sympy" in signal:
        return "sympy_other"
    if _is_pytest_repository_signal(signal):
        return "pytest"
    return "unknown"


def _repository_case_signal(case: dict[str, Any]) -> str:
    parts: list[str] = [str(case.get("id", "")), str(case.get("source_repository", ""))]
    for side in ("baseline", "evolved"):
        item = case.get(side, {})
        if not isinstance(item, dict):
            continue
        parts.extend(str(path) for path in item.get("changed_paths", []) or [])
        for result_key in ("test_result", "regression_result"):
            result = item.get(result_key, {})
            if isinstance(result, dict):
                parts.append(str(result.get("stdout", ""))[-1000:])
                parts.append(str(result.get("stderr", ""))[-1000:])
    return "\n".join(parts).casefold()


def _repository_fixture_signal(fixture: RepositoryFixture) -> str:
    return "\n".join(
        [
            fixture.id,
            fixture.issue,
            fixture.test_command,
            fixture.regression_command,
            *fixture.allowed_paths,
            *fixture.expected_tests,
        ]
    ).casefold()


def _repository_fixture_family(fixture: RepositoryFixture) -> str:
    signal = _repository_fixture_signal(fixture)
    if "flask" in signal:
        return "flask"
    if "requests" in signal:
        return "requests"
    if "sympy/printing" in signal or any(
        token in signal
        for token in ("latex", "pretty", "ccode", "mathematica", "printer")
    ):
        return "sympy_printing"
    if "sympy/matrices" in signal or "matrix" in signal:
        return "sympy_matrices"
    if "sympy/functions" in signal:
        return "sympy_functions"
    if "sympy/integrals" in signal:
        return "sympy_integrals"
    if "sympy/core" in signal or any(token in signal for token in ("evalf", "test_arit")):
        return "sympy_core"
    if "sympy" in signal:
        return "sympy_other"
    if _is_pytest_repository_signal(signal):
        return "pytest"
    return "unknown"


def _is_pytest_repository_signal(signal: str) -> bool:
    return any(
        token in signal
        for token in (
            "pytest-dev",
            "src/_pytest",
            "_pytest/",
            "testing/test_assertrewrite.py",
        )
    )


def _normalize_route_family_filter(
    families: tuple[str, ...] | list[str] | None,
) -> frozenset[str] | None:
    if families is None:
        return None
    return frozenset(
        str(family).strip()
        for family in families
        if str(family).strip()
    )


def _repository_task_route(
    fixture: RepositoryFixture,
    promoted_families: frozenset[str] | None = None,
) -> RepositoryTaskRoute:
    family = _repository_fixture_family(fixture)
    route = _ROUTED_REPOSITORY_SKILLS.get(family)
    if route is None:
        return RepositoryTaskRoute(
            family=family,
            action="skip",
            skill_name="",
            skill="",
            hint=(
                "Task router found no high-confidence family skill. Do not inject a "
                "generic evolved Skill; rely on the base repository repair prompt."
            ),
            reason="no high-confidence routed skill for this fixture family",
        )
    skill_name, skill = route
    if promoted_families is not None and family not in promoted_families:
        return RepositoryTaskRoute(
            family=family,
            action="skip",
            skill_name=skill_name,
            skill="",
            hint=(
                f"Task router matched `{skill_name}` for family `{family}`, but this "
                "family Skill is not in the promoted route set. Do not inject the "
                "candidate Skill; rely on the baseline repository repair prompt."
            ),
            reason="routed family skill not promoted by task-router gate",
        )
    return RepositoryTaskRoute(
        family=family,
        action="inject",
        skill_name=skill_name,
        skill=skill,
        hint=(
            f"Task router selected `{skill_name}` for family `{family}`. Follow this "
            "family-specific Skill over generic repository repair habits."
        ),
        reason="fixture issue, test command, expected tests, and allowed paths matched route signals",
    )


def _repository_route_hint(route: RepositoryTaskRoute) -> str:
    if route.action == "inject":
        return f"{route.hint}\n\nRouted Skill Overlay:\n{route.skill}"
    return route.hint


def _compose_repository_routed_skill(
    candidate_skill: str,
    route: RepositoryTaskRoute,
) -> str:
    base = candidate_skill.strip()
    overlay = route.skill.strip()
    if not base:
        return overlay
    if not overlay:
        return base
    return (
        f"{base}\n\n"
        "# Routed Task Overlay\n"
        "The following family-specific overlay refines the generic candidate Skill "
        "for this fixture. Keep the generic safety, scope, and verification rules, "
        "but use the overlay to choose the repair path.\n\n"
        f"{overlay}"
    )


def _repository_strategy_hint(fixture: RepositoryFixture) -> str:
    signal = "\n".join(
        [
            fixture.id,
            fixture.test_command,
            fixture.regression_command,
            *fixture.allowed_paths,
            *fixture.expected_tests,
        ]
    ).casefold()
    if "sympy/printing" in signal or any(
        token in signal
        for token in ("latex", "pretty", "ccode", "mathematica", "printer")
    ):
        return (
            "This looks like a SymPy printer task. First inspect the target printer "
            "class and nearby _print_* methods, reproduce the exact failing output, "
            "then add the narrow dispatch/formatting method in the allowed printer file. "
            "Avoid broad expression simplification changes."
        )
    if "sympy/matrices" in signal or "matrix" in signal:
        return (
            "This looks like a SymPy matrix task. Focus on shape/index invariants and "
            "the specific constructor or join helper under test. Preserve zero-dimension "
            "matrix behavior and run the targeted regression subset after the patch."
        )
    if "sympy/functions" in signal:
        return (
            "This looks like a SymPy functions task. Patch the smallest evaluation, "
            "rewrite, or printing branch for the failing function, and avoid changing "
            "global simplification behavior unless the target test requires it."
        )
    if "sympy/core" in signal or any(token in signal for token in ("evalf", "test_arit")):
        return (
            "This looks like a SymPy core task. Trace the failing constructor/eval path "
            "with a minimal expression, add the narrow guard where the incorrect canonical "
            "form is produced, and avoid broad arithmetic rewrites."
        )
    if "sympy/integrals" in signal:
        return (
            "This looks like a SymPy integration task. Inspect the failing algorithm branch "
            "and preserve existing heuristics; prefer a narrow condition with nearby tests."
        )
    if "flask" in signal:
        return (
            "This looks like a Flask behavior task. Locate the public API boundary under "
            "test, add the minimal validation or normalization in the allowed source file, "
            "and keep compatibility with existing regression tests."
        )
    if "requests" in signal:
        return (
            "This looks like a Requests utility task. Patch the narrow URL/header/body "
            "handling path under test and avoid changing unrelated networking behavior."
        )
    if _is_pytest_repository_signal(signal):
        return (
            "This looks like a pytest internals task. Reproduce the assertion/rewrite "
            "failure, patch the narrow internal helper, and run the targeted regression tests."
        )
    return (
        "Use the allowed paths and failing assertion to choose one narrow source patch; "
        "avoid research-only loops and verify target plus regression commands."
    )


def _increment_count(counts: dict[str, int], key: str) -> None:
    counts[key] = counts.get(key, 0) + 1


def _render_failure_taxonomy_markdown(taxonomy: dict[str, Any]) -> list[str]:
    if not taxonomy:
        return []
    lines = ["", "## Failure Taxonomy", ""]
    lines.extend(_render_count_table("Outcome Counts", taxonomy.get("outcome_counts", {})))
    lines.extend(_render_count_table("Task Families", taxonomy.get("task_family_counts", {})))
    lines.extend(_render_count_table("Evolved Failure Types", taxonomy.get("evolved_failure_type_counts", {})))
    targeted = taxonomy.get("targeted_case_ids", {})
    if isinstance(targeted, dict):
        lines.extend(["", "### Targeted Case Buckets", "", "| Bucket | Case IDs |", "|---|---|"])
        for key in sorted(targeted):
            values = targeted.get(key, [])
            if not values:
                continue
            lines.append(f"| {key} | {', '.join(str(value) for value in values)} |")
    return lines


def _render_route_impacts_markdown(route_impacts: dict[str, Any]) -> list[str]:
    families = route_impacts.get("families", {}) if isinstance(route_impacts, dict) else []
    if not isinstance(families, list) or not families:
        return []
    lines = [
        "",
        "## Route Family Impact",
        "",
        "| Family | Cases | Injected | Skipped | Baseline | Evolved | Lift | Regressions | Calls Δ | Tokens Δ | Promote | Reason |",
        "|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|",
    ]
    for row in families:
        if not isinstance(row, dict):
            continue
        calls_delta = (
            int(row.get("model_call_delta_total", 0) or 0)
            + int(row.get("tool_call_delta_total", 0) or 0)
        )
        lines.append(
            "| "
            f"{row.get('family', '')} | "
            f"{int(row.get('case_count', 0) or 0)} | "
            f"{int(row.get('injected_case_count', 0) or 0)} | "
            f"{int(row.get('skipped_case_count', 0) or 0)} | "
            f"{_format_rate(row.get('baseline_success_rate'))} | "
            f"{_format_rate(row.get('evolved_success_rate'))} | "
            f"{_format_signed(row.get('success_rate_lift_percentage_points'))} pp | "
            f"{int(row.get('regression_count', 0) or 0)} | "
            f"{_format_signed(calls_delta, decimals=0)} | "
            f"{_format_signed(row.get('total_token_delta_total'), decimals=0)} | "
            f"{_yes_no(row.get('promotion_recommended'))} | "
            f"{row.get('promotion_reason', '')} |"
        )
    promoted = route_impacts.get("promoted_families", [])
    if isinstance(promoted, list) and promoted:
        lines.extend([
            "",
            "Promoted route families: " + ", ".join(f"`{family}`" for family in promoted),
        ])
    return lines


def _render_count_table(title: str, counts: Any) -> list[str]:
    if not isinstance(counts, dict) or not counts:
        return []
    lines = ["", f"### {title}", "", "| Bucket | Count |", "|---|---:|"]
    for key, value in sorted(counts.items()):
        lines.append(f"| {key} | {int(value)} |")
    return lines


def _yes_no(value: Any) -> str:
    return "yes" if bool(value) else "no"


def _failure_label(label: str, item: dict[str, Any]) -> str:
    status = str(item.get("status", "")).strip()
    if status not in {"provider-failed", "runner-failed"}:
        return ""
    error_type = str(item.get("error_type", "")).strip()
    suffix = f"/{error_type}" if error_type else ""
    return f"{label}:{status}{suffix}"


def _render_case_side(label: str, item: dict[str, Any]) -> str:
    lines = [
        f"**{label}**",
        f"- Status: `{item.get('status', '')}`",
        f"- Task success: `{_yes_no(item.get('task_success'))}`",
        f"- Tests passed: `{_yes_no(item.get('tests_passed'))}`",
        f"- Regression free: `{_yes_no(item.get('regression_free'))}`",
        f"- Changed paths: `{', '.join(item.get('changed_paths', [])) or '-'}`",
        f"- Out-of-scope changes: `{', '.join(item.get('out_of_scope_changes', [])) or '-'}`",
        f"- Forbidden changes: `{', '.join(item.get('forbidden_changes', [])) or '-'}`",
        f"- Patch size: `{_patch_total(item)}`",
        f"- Tokens: `{item.get('input_tokens', 0)} in / {item.get('output_tokens', 0)} out`",
        f"- Model calls: `{item.get('model_call_count', 0)}`",
        f"- Tool calls: `{item.get('tool_call_count', 0)}`",
        f"- Permission denied: `{item.get('permission_denied', 0)}`",
        f"- Rewind used: `{_yes_no(item.get('rewind_used'))}`",
        f"- Test command: `{_result_status(item.get('test_result'))}`",
        f"- Regression command: `{_result_status(item.get('regression_result'))}`",
    ]
    if item.get("agent_run_skipped"):
        lines.insert(2, "- Agent run skipped: `yes`")
        if item.get("selected_policy"):
            lines.insert(3, f"- Selected policy: `{item.get('selected_policy')}`")
    return "\n".join(lines)


def _result_status(result: Any) -> str:
    if not isinstance(result, dict):
        return "not configured"
    status = str(result.get("status", "")).strip() or "unknown"
    exit_code = result.get("exit_code")
    if exit_code is None:
        return status
    return f"{status} ({exit_code})"


def load_repository_fixtures(root: str | Path) -> list[RepositoryFixture]:
    """Load and validate repository benchmark cases under ``root``."""
    fixture_root = Path(root)
    if not fixture_root.is_dir():
        raise ValueError(f"invalid repository fixture root: {fixture_root}")

    try:
        case_directories = sorted(
            (path for path in fixture_root.iterdir() if path.is_dir()),
            key=lambda path: path.name,
        )
    except OSError as exc:
        raise ValueError(f"invalid repository fixture root: {fixture_root}: {exc}") from exc

    fixtures: list[RepositoryFixture] = []
    for case_directory in case_directories:
        case_id = case_directory.name
        try:
            fixtures.append(_load_repository_fixture(case_directory))
        except (
            json.JSONDecodeError,
            OSError,
            RecursionError,
            TypeError,
            ValueError,
            UnicodeError,
        ) as exc:
            raise ValueError(f"invalid repository fixture {case_id}: {exc}") from exc
    return fixtures


def _load_repository_fixture(case_directory: Path) -> RepositoryFixture:
    repository = case_directory / "repository"
    issue_path = case_directory / "issue.md"
    expected_path = case_directory / "expected.json"

    if case_directory.is_symlink():
        raise ValueError("case directory cannot be a symlink")
    if repository.is_symlink():
        raise ValueError("repository/ cannot be a symlink")
    if not repository.is_dir():
        raise ValueError("repository/ must be a directory")
    if issue_path.is_symlink():
        raise ValueError("issue.md cannot be a symlink")
    if not issue_path.is_file():
        raise ValueError("issue.md must be a file")
    if expected_path.is_symlink():
        raise ValueError("expected.json cannot be a symlink")
    if not expected_path.is_file():
        raise ValueError("expected.json must be a file")
    _reject_repository_symlinks(repository)

    issue = issue_path.read_text(encoding="utf-8").strip()
    if not issue:
        raise ValueError("issue.md cannot be empty")

    expected = json.loads(expected_path.read_text(encoding="utf-8"))
    if not isinstance(expected, dict):
        raise TypeError("expected.json must contain an object")

    test_command = _required_nonempty_string(expected, "test_command")
    regression_command = expected.get("regression_command", "")
    if not isinstance(regression_command, str):
        raise TypeError("regression_command must be a string")

    return RepositoryFixture(
        id=case_directory.name,
        repository=repository,
        issue=issue,
        test_command=test_command,
        regression_command=regression_command.strip(),
        expected_tests=_path_rules(expected, "expected_tests", require_nonempty=True),
        allowed_paths=_path_rules(expected, "allowed_paths", require_nonempty=True),
        forbidden_paths=_path_rules(expected, "forbidden_paths", require_nonempty=False),
    )


def _reject_repository_symlinks(repository: Path) -> None:
    pending = [repository]
    while pending:
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                if entry.is_symlink():
                    relative_path = Path(entry.path).relative_to(repository)
                    raise ValueError(
                        f"repository entry cannot be a symlink: {relative_path}"
                    )
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))


def _required_nonempty_string(data: dict[str, Any], field: str) -> str:
    if field not in data:
        raise ValueError(f"missing required field: {field}")
    value = data[field]
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a string")
    value = value.strip()
    if not value:
        raise ValueError(f"{field} cannot be empty")
    return value


def _path_rules(
    data: dict[str, Any],
    field: str,
    *,
    require_nonempty: bool,
) -> tuple[str, ...]:
    if field not in data:
        raise ValueError(f"missing required field: {field}")
    values = data[field]
    if not isinstance(values, list):
        raise TypeError(f"{field} must be a list")
    if require_nonempty and not values:
        raise ValueError(f"{field} cannot be empty")

    rules: list[str] = []
    for value in values:
        if not isinstance(value, str):
            raise TypeError(f"{field} entries must be strings")
        rule = value.strip()
        path = PurePosixPath(rule)
        windows_path = PureWindowsPath(rule)
        if not rule:
            raise ValueError(f"{field} entries cannot be empty")
        if (
            "\\" in rule
            or path.is_absolute()
            or ".." in path.parts
            or windows_path.drive
        ):
            raise ValueError(f"{field} entry must be a relative POSIX path: {value!r}")
        rules.append(rule)
    return tuple(rules)

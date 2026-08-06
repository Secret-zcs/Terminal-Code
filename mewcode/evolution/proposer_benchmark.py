"""Real-model benchmark for Fork Skill Proposer candidates."""

from __future__ import annotations

import json
import tempfile
import time
from pathlib import Path
from typing import Any

from mewcode.client import LLMClient
from mewcode.evolution.benchmark import (
    DEFAULT_BASELINE_SKILL,
    load_benchmark_cases,
    score_skill_text,
)
from mewcode.evolution.engine import EvolutionEngine
from mewcode.evolution.fork_skill_proposer_agent import ForkSkillProposerAgent


async def run_proposer_benchmark(
    client: LLMClient,
    dataset_path: str | Path,
    *,
    max_cases: int = 10,
    workspace_root: str | Path | None = None,
) -> dict[str, Any]:
    """Run model-generated candidates through the same gates used in production."""
    cases = load_benchmark_cases(dataset_path)[: max(0, int(max_cases))]
    own_workspace = workspace_root is None
    temporary = (
        tempfile.TemporaryDirectory(prefix="mewcode-proposer-benchmark-")
        if own_workspace
        else None
    )
    root = Path(temporary.name) if temporary is not None else Path(workspace_root)
    root.mkdir(parents=True, exist_ok=True)
    agent = ForkSkillProposerAgent(client)
    case_results: list[dict[str, Any]] = []
    usage = {"input_tokens": 0, "output_tokens": 0}
    try:
        for index, case in enumerate(cases, 1):
            case_root = root / f"case_{index:04d}"
            case_root.mkdir(parents=True, exist_ok=True)
            baseline = score_skill_text(case, DEFAULT_BASELINE_SKILL)
            started = time.perf_counter()
            result: dict[str, Any] = {
                "id": case.id,
                "source": case.source,
                "task_family": case.task_family,
                "baseline": baseline,
                "status": "schema-failed",
                "proposer_attempts": 0,
                "elapsed_seconds": 0.0,
            }
            try:
                candidate = await agent.propose(
                    original_skill="",
                    evidence_summary=_render_case_evidence(case),
                    task_markdown=(
                        "Create one narrow project Skill for this derived task family. "
                        "Cover every required term and avoid every forbidden term."
                    ),
                )
                _add_usage(usage, candidate.get("usage", {}))
                result["proposer_attempts"] = int(
                    candidate.get("usage", {}).get("attempts", 1) or 1
                )
                _validate_create_candidate(candidate)
                result["schema_passed"] = True
                engine = EvolutionEngine(case_root)
                proposal = engine.propose_skill(
                    name=candidate["name"],
                    description=candidate["description"],
                    body=candidate["body"],
                    allowed_tools=candidate["allowedTools"],
                    mode=candidate["mode"],
                    context=candidate["context"],
                    rationale=candidate["rationale"],
                    risk="medium",
                )
                validation = engine.validate(proposal)
                result["static_policy_passed"] = validation.ok
                result["static_policy_warnings"] = validation.warnings
                if not validation.ok:
                    result["status"] = "static-policy-failed"
                    result["errors"] = validation.errors
                    continue

                for round_number in range(1, 4):
                    engine.add_eval_case(
                        proposal.id,
                        task=(
                            f"{case.task} Replay execution round {round_number}."
                        ),
                        must_contain=list(case.required_terms),
                        must_not_contain=list(case.forbidden_terms),
                    )
                eval_ok, eval_message = engine.evaluate(proposal.id)
                result["eval_passed"] = eval_ok
                result["eval_message"] = eval_message
                if not eval_ok:
                    result["status"] = "eval-failed"
                    continue

                execution_ok, execution_message = engine.run_execution_eval(
                    proposal.id,
                    min_cases=3,
                )
                report = _read_execution_summary(engine, proposal.id)
                result["execution_eval"] = {
                    "passed": execution_ok,
                    "message": execution_message,
                    "rounds": (
                        f"{report.get('passed', 0)}/{report.get('total', 0)}"
                    ),
                    "runner": report.get("runner", ""),
                }
                result["candidate"] = score_skill_text(
                    case,
                    candidate["body"],
                )
                if not execution_ok:
                    result["status"] = "execution-eval-failed"
                    continue
                result["status"] = "approval-ready"
            except Exception as exc:
                if not result.get("schema_passed"):
                    result["schema_passed"] = False
                    result["status"] = "schema-failed"
                else:
                    result["status"] = "runner-failed"
                result["error_type"] = type(exc).__name__
                result["error"] = str(exc)[:4000]
                if not result["proposer_attempts"]:
                    result["proposer_attempts"] = int(
                        getattr(exc, "attempts", 1) or 1
                    )
                _add_usage(usage, getattr(exc, "usage", {}))
            finally:
                result["elapsed_seconds"] = round(
                    time.perf_counter() - started,
                    6,
                )
                case_results.append(result)
    finally:
        if temporary is not None:
            temporary.cleanup()

    summary = {
        "total_cases": len(case_results),
        "schema_passed": sum(
            1 for item in case_results if item.get("schema_passed")
        ),
        "static_policy_passed": sum(
            1 for item in case_results if item.get("static_policy_passed")
        ),
        "eval_passed": sum(1 for item in case_results if item.get("eval_passed")),
        "execution_eval_passed": sum(
            1
            for item in case_results
            if item.get("execution_eval", {}).get("passed")
        ),
        "approval_ready": sum(
            1 for item in case_results if item.get("status") == "approval-ready"
        ),
        "baseline_passed": sum(
            1 for item in case_results if item.get("baseline", {}).get("passed")
        ),
    }
    return {
        "dataset": str(dataset_path),
        "method": "real_fork_skill_proposer_then_deterministic_gates",
        "summary": summary,
        "usage": usage,
        "cases": case_results,
    }


def _render_case_evidence(case) -> str:
    return json.dumps({
        "source": case.source,
        "source_url": case.source_url,
        "task_family": case.task_family,
        "task": case.task,
        "required_terms": case.required_terms,
        "forbidden_terms": case.forbidden_terms,
        "notes": case.notes,
    }, ensure_ascii=False, indent=2)


def _validate_create_candidate(candidate: dict[str, Any]) -> None:
    if candidate.get("action") != "create":
        raise ValueError("benchmark proposer candidate must use action=create")


def _add_usage(total: dict[str, int], usage: object) -> None:
    if not isinstance(usage, dict):
        return
    total["input_tokens"] += int(usage.get("input_tokens", 0) or 0)
    total["output_tokens"] += int(usage.get("output_tokens", 0) or 0)


def _read_execution_summary(engine: EvolutionEngine, proposal_id: str) -> dict:
    path = engine.execution_eval_report_path(proposal_id)
    if not path.exists():
        return {}
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    return {
        "passed": int(summary.get("passed", 0) or 0),
        "total": int(summary.get("total", 0) or 0),
        "runner": report.get("runner", "") if isinstance(report, dict) else "",
    }


def render_proposer_benchmark_markdown(result: dict[str, Any]) -> str:
    summary = result.get("summary", {})
    usage = result.get("usage", {})
    lines = [
        "# Fork Skill Proposer Benchmark",
        "",
        "## Methodology",
        "",
        "- Each case is sent to an isolated, tool-free Fork Skill Proposer.",
        "- The candidate is written only to a temporary project sandbox.",
        "- A candidate is approval-ready only after schema, static policy, eval, and 3/3 execution eval pass.",
        "- Baseline is scored without an LLM; this benchmark measures candidate generation and gate outcomes.",
        "",
        "## Summary",
        "",
        f"- Dataset: `{result.get('dataset', '')}`",
        f"- Cases: `{summary.get('total_cases', 0)}`",
        f"- Schema passed: `{summary.get('schema_passed', 0)}`",
        f"- Static policy passed: `{summary.get('static_policy_passed', 0)}`",
        f"- Eval passed: `{summary.get('eval_passed', 0)}`",
        f"- Execution eval passed: `{summary.get('execution_eval_passed', 0)}`",
        f"- Approval ready: `{summary.get('approval_ready', 0)}`",
        f"- Baseline passed: `{summary.get('baseline_passed', 0)}`",
        f"- Input tokens: `{usage.get('input_tokens', 0)}`",
        f"- Output tokens: `{usage.get('output_tokens', 0)}`",
        "",
        "## Cases",
        "",
        "| Case | Baseline | Status | Attempts | Execution | Seconds |",
        "|---|---|---|---:|---|---:|",
    ]
    for case in result.get("cases", []):
        execution = case.get("execution_eval", {}).get("rounds", "-")
        lines.append(
            f"| {case.get('id')} | "
            f"{'pass' if case.get('baseline', {}).get('passed') else 'fail'} | "
            f"{case.get('status')} | {case.get('proposer_attempts', 0)} | "
            f"{execution} | "
            f"{case.get('elapsed_seconds', 0)} |"
        )
    lines.append("")
    return "\n".join(lines)

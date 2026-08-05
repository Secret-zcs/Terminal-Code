"""Automatic review pass for self-evolution candidates."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mewcode.evolution.engine import EvolutionEngine, MIN_EXECUTION_EVAL_CASES
from mewcode.evolution.models import SelfEvolutionReviewRun, new_evolution_id


FORK_REVIEWER_STALE_SECONDS = 60 * 60


def format_review_notification(result: dict) -> str:
    requests = result.get("requests", [])
    blocked = result.get("blocked_generated_candidates", [])
    expired = [
        str(run_id).strip()
        for run_id in result.get("expired_review_run_ids", [])
        if str(run_id).strip()
    ]
    if result.get("status") == "busy":
        active_id = str(result.get("active_review_run_id", "")).strip()
        report = str(result.get("active_review_report", "")).strip()
        message = "Self-evolution review already running."
        if active_id:
            message += f" Active run: {active_id}."
        if report:
            message += f" Report: {report}."
        if expired:
            message += " Recovered stale review run(s): " + ", ".join(expired) + "."
        return message
    if result.get("status") == "started":
        run = result.get("review_run")
        run_id = str(getattr(run, "id", "") or "").strip()
        artifacts = getattr(run, "artifacts", {}) or {}
        task = str(artifacts.get("task", "")).strip()
        report = str(artifacts.get("report", "")).strip()
        message = "Self-evolution review started."
        if run_id:
            message += f" Run: {run_id}."
        if task:
            message += f" Task: {task}."
        if report:
            message += f" Report: {report}."
        return message
    if not requests and not blocked and not expired:
        return ""
    lines = []
    if expired:
        lines.append("Self-evolution recovered stale review run(s):")
        for run_id in expired:
            lines.append(f"- {run_id}")
    if requests:
        if lines:
            lines.append("")
        lines.append("Self-evolution approval request(s) ready:")
        for request in requests:
            request_id = str(getattr(request, "id", "") or "").strip()
            request_prefix = f"{request_id} / " if request_id else ""
            lines.append(
                f"- {request_prefix}{request.proposal_id} / {request.skill_name} "
                f"mode={request.approval_mode} report={request.eval_report_markdown}"
            )
    if blocked:
        if lines:
            lines.append("")
        lines.append("Self-evolution blocked generated candidate(s):")
        for item in blocked:
            lines.append(
                f"- {item.get('proposal_id')} / {item.get('skill_name')} "
                f"reason={item.get('reason')}"
            )
    return "\n".join(lines)


def review_ready_skill_candidates(
    project_root: str | Path,
    self_evolution_config: Any,
) -> dict:
    """Submit approval requests for evaluated skill candidates.

    This function intentionally does not create skills, approve proposals, or
    promote candidates. It only turns already-tested candidates into pending
    approval requests when self-evolution is enabled.
    """
    if not getattr(self_evolution_config, "enabled", False):
        return _empty_review_result("disabled")

    approval_mode, rollback_threshold, rollback_events = _review_config_values(
        self_evolution_config
    )
    engine = EvolutionEngine(project_root)
    expired_review_runs: list[SelfEvolutionReviewRun] = []
    active_run = _active_fork_reviewer_run(
        engine,
        expired_runs=expired_review_runs,
    )
    if active_run is not None:
        result = _empty_review_result("busy")
        result["active_review_run_id"] = active_run.id
        result["active_review_report"] = active_run.artifacts.get("report", "")
        result["expired_review_run_ids"] = [run.id for run in expired_review_runs]
        return result

    review_run = _start_fork_reviewer_run(
        engine,
        approval_mode=approval_mode,
        trusted_auto_rollback_threshold=rollback_threshold,
        trusted_auto_rollback_events=rollback_events,
    )
    try:
        result = _review_enabled_skill_candidates(
            engine,
            approval_mode=approval_mode,
            trusted_auto_rollback_threshold=rollback_threshold,
            trusted_auto_rollback_events=rollback_events,
        )
    except Exception as exc:
        _finish_fork_reviewer_run(
            engine,
            review_run,
            _empty_review_result("failed"),
            status="failed",
            error=str(exc),
        )
        raise

    result["expired_review_run_ids"] = [run.id for run in expired_review_runs]
    result["review_run"] = _finish_fork_reviewer_run(engine, review_run, result)
    return result


def start_fork_reviewer_run(
    project_root: str | Path,
    self_evolution_config: Any,
) -> dict:
    """Create a resumable fork reviewer run without executing the review pass."""
    if not getattr(self_evolution_config, "enabled", False):
        return _empty_review_result("disabled")

    approval_mode, rollback_threshold, rollback_events = _review_config_values(
        self_evolution_config
    )
    engine = EvolutionEngine(project_root)
    expired_review_runs: list[SelfEvolutionReviewRun] = []
    active_run = _active_fork_reviewer_run(
        engine,
        expired_runs=expired_review_runs,
    )
    if active_run is not None:
        result = _empty_review_result("busy")
        result["active_review_run_id"] = active_run.id
        result["active_review_report"] = active_run.artifacts.get("report", "")
        result["expired_review_run_ids"] = [run.id for run in expired_review_runs]
        return result

    review_run = _start_fork_reviewer_run(
        engine,
        approval_mode=approval_mode,
        trusted_auto_rollback_threshold=rollback_threshold,
        trusted_auto_rollback_events=rollback_events,
    )
    result = _empty_review_result("started")
    result["expired_review_run_ids"] = [run.id for run in expired_review_runs]
    result["review_run"] = review_run
    return result


def complete_fork_reviewer_run(
    project_root: str | Path,
    review_run_id: str,
    self_evolution_config: Any,
) -> dict:
    """Resume and finish a previously started fork reviewer run."""
    if not getattr(self_evolution_config, "enabled", False):
        return _empty_review_result("disabled")

    engine = EvolutionEngine(project_root)
    review_run = _get_fork_reviewer_run(engine, review_run_id)
    if review_run is None:
        return _empty_review_result("missing")
    if review_run.status != "running":
        result = _empty_review_result("not-running")
        result["review_run"] = review_run
        return result

    fallback_mode, fallback_threshold, fallback_events = _review_config_values(
        self_evolution_config
    )
    approval_mode = review_run.approval_mode or fallback_mode
    rollback_threshold, rollback_events = _review_run_trusted_auto_values(
        review_run,
        fallback_threshold=fallback_threshold,
        fallback_events=fallback_events,
    )
    try:
        result = _review_enabled_skill_candidates(
            engine,
            approval_mode=approval_mode,
            trusted_auto_rollback_threshold=rollback_threshold,
            trusted_auto_rollback_events=rollback_events,
        )
    except Exception as exc:
        _finish_fork_reviewer_run(
            engine,
            review_run,
            _empty_review_result("failed"),
            status="failed",
            error=str(exc),
        )
        raise

    result["expired_review_run_ids"] = []
    result["review_run"] = _finish_fork_reviewer_run(engine, review_run, result)
    return result


def _review_config_values(self_evolution_config: Any) -> tuple[str, int, list[str]]:
    approval_mode = getattr(self_evolution_config, "skill_approval_mode", "manual")
    rollback_threshold = getattr(
        self_evolution_config,
        "trusted_auto_rollback_threshold",
        1,
    )
    rollback_events = getattr(
        self_evolution_config,
        "trusted_auto_rollback_events",
        ["failure", "user_feedback"],
    )
    return approval_mode, rollback_threshold, list(rollback_events)


def _get_fork_reviewer_run(
    engine: EvolutionEngine,
    review_run_id: str,
) -> SelfEvolutionReviewRun | None:
    clean_id = review_run_id.strip()
    if not clean_id:
        return None
    for run in reversed(engine.store.load_self_evolution_review_runs()):
        if run.mode == "fork_reviewer" and run.id == clean_id:
            return run
    return None


def _review_run_trusted_auto_values(
    run: SelfEvolutionReviewRun,
    *,
    fallback_threshold: int,
    fallback_events: list[str],
) -> tuple[int, list[str]]:
    policy = run.policy.get("trusted_auto_policy", {})
    if not isinstance(policy, dict):
        return fallback_threshold, fallback_events
    threshold = int(policy.get("rollback_threshold", fallback_threshold) or 1)
    events = policy.get("rollback_events", fallback_events)
    return max(1, threshold), list(events or fallback_events)


def _active_fork_reviewer_run(
    engine: EvolutionEngine,
    *,
    expired_runs: list[SelfEvolutionReviewRun] | None = None,
) -> SelfEvolutionReviewRun | None:
    now = time.time()
    for run in reversed(engine.store.load_self_evolution_review_runs()):
        if run.mode != "fork_reviewer" or run.status != "running":
            continue
        if _fork_reviewer_run_is_stale(run, now=now):
            _expire_stale_fork_reviewer_run(engine, run, now=now)
            if expired_runs is not None:
                expired_runs.append(run)
            continue
        return run
    return None


def _fork_reviewer_run_is_stale(
    run: SelfEvolutionReviewRun,
    *,
    now: float,
) -> bool:
    created_at = float(run.created_at or 0.0)
    return created_at > 0 and now - created_at > FORK_REVIEWER_STALE_SECONDS


def _expire_stale_fork_reviewer_run(
    engine: EvolutionEngine,
    run: SelfEvolutionReviewRun,
    *,
    now: float,
) -> None:
    run.status = "failed"
    run.completed_at = now
    run.error = (
        "stale fork reviewer lock expired after "
        f"{FORK_REVIEWER_STALE_SECONDS} seconds"
    )
    engine.store.update_self_evolution_review_run(run)


def _empty_review_result(status: str) -> dict:
    return {
        "status": status,
        "requests": [],
        "skipped": [],
        "generated_candidates": [],
        "generated_candidate_reviews": [],
        "generated_eval_cases": [],
        "generated_evaluations": [],
        "generated_execution_evals": [],
        "blocked_generated_candidates": [],
        "auto_promotions": [],
        "auto_quarantines": [],
        "ingested_usage": [],
        "review_run": None,
        "active_review_run_id": "",
        "active_review_report": "",
        "expired_review_run_ids": [],
    }


def _review_enabled_skill_candidates(
    engine: EvolutionEngine,
    *,
    approval_mode: str,
    trusted_auto_rollback_threshold: int = 1,
    trusted_auto_rollback_events: list[str] | None = None,
) -> dict:
    requests = []
    skipped: list[dict] = []
    ingested_usage = _ingest_evidence_as_skill_usage(engine)
    auto_quarantines = _auto_quarantine_trusted_auto_skills(
        engine,
        approval_mode=approval_mode,
        threshold=trusted_auto_rollback_threshold,
        rollback_events=trusted_auto_rollback_events,
    )

    for proposal in engine.store.load_proposals():
        if proposal.target != "skill":
            continue
        if proposal.status != "proposed":
            skipped.append({
                "proposal_id": proposal.id,
                "reason": f"status is {proposal.status}",
            })
            continue
        if engine.store.get_pending_skill_approval_request(proposal.id) is not None:
            continue
        try:
            request = engine.submit_skill_approval_request(
                proposal.id,
                approval_mode=approval_mode,
                source="self-evolution-review",
            )
        except ValueError as exc:
            skipped.append({"proposal_id": proposal.id, "reason": str(exc)})
            continue
        requests.append(request)

    generated_candidates, generated_candidate_reviews = (
        _generate_usage_patch_candidates(
            engine,
            suppressed_skills=(
                _trusted_auto_managed_skill_names(engine)
                if approval_mode == "trusted-auto"
                else set()
            ),
        )
    )
    generated_eval_cases = _materialize_safe_eval_suggestions(
        engine,
        generated_candidate_reviews,
    )
    generated_evaluations = _evaluate_generated_candidates(
        engine,
        generated_eval_cases,
    )
    generated_execution_evals = _run_execution_evals_for_generated_candidates(
        engine,
        generated_evaluations,
    )
    blocked_generated_candidates = _block_failed_generated_candidates(
        engine,
        generated_execution_evals,
    )
    generated_requests, generated_request_skips, auto_promotions = (
        _submit_generated_approval_requests(
            engine,
            generated_execution_evals,
            approval_mode=approval_mode,
        )
    )
    requests.extend(generated_requests)
    skipped.extend(generated_request_skips)

    return {
        "status": (
            "submitted"
            if requests
            else "quarantined"
            if auto_quarantines
            else "auto-promoted"
            if auto_promotions
            else "blocked"
            if blocked_generated_candidates
            else "generated"
            if generated_candidates
            else "idle"
        ),
        "requests": requests,
        "skipped": skipped,
        "generated_candidates": generated_candidates,
        "generated_candidate_reviews": generated_candidate_reviews,
        "generated_eval_cases": generated_eval_cases,
        "generated_evaluations": generated_evaluations,
        "generated_execution_evals": generated_execution_evals,
        "blocked_generated_candidates": blocked_generated_candidates,
        "auto_promotions": auto_promotions,
        "auto_quarantines": auto_quarantines,
        "ingested_usage": ingested_usage,
        "review_run": None,
    }


def _start_fork_reviewer_run(
    engine: EvolutionEngine,
    *,
    approval_mode: str,
    trusted_auto_rollback_threshold: int = 1,
    trusted_auto_rollback_events: list[str] | None = None,
) -> SelfEvolutionReviewRun:
    run_id = new_evolution_id("review")
    run_dir = engine.project_root / ".mewcode" / "evolution" / "review_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    artifacts = {
        "input": _project_relative(engine, run_dir / "input.json"),
        "policy": _project_relative(engine, run_dir / "policy.json"),
        "task": _project_relative(engine, run_dir / "task.md"),
        "output": _project_relative(engine, run_dir / "output.json"),
        "report": _project_relative(engine, run_dir / "report.md"),
    }
    trusted_auto_policy = {
        "auto_promote_scope": "same_pass_generated_candidates_only",
        "rollback_threshold": max(1, int(trusted_auto_rollback_threshold)),
        "rollback_events": list(
            trusted_auto_rollback_events or ["failure", "user_feedback"]
        ),
    }
    policy = {
        "mode": "fork_reviewer",
        "can_record_evidence": True,
        "can_generate_candidate": True,
        "can_generate_eval_case": True,
        "can_run_eval": True,
        "can_submit_approval_request": True,
        "can_approve": False,
        "can_promote": False,
        "project_write": "disabled",
        "network": "disabled",
        "trusted_auto_policy": trusted_auto_policy,
    }
    input_payload = {
        "approval_mode": approval_mode,
        "trusted_auto_policy": trusted_auto_policy,
        "counts": {
            "evidence": len(engine.store.load_evidence()),
            "proposals": len(engine.store.load_proposals()),
            "approval_requests": len(engine.store.load_skill_approval_requests()),
            "skill_usage": len(engine.load_skill_usage()),
        },
        "policy": policy,
    }
    (engine.project_root / artifacts["input"]).write_text(
        json.dumps(input_payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (engine.project_root / artifacts["policy"]).write_text(
        json.dumps(policy, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (engine.project_root / artifacts["task"]).write_text(
        _render_fork_reviewer_task(input_payload, policy),
        encoding="utf-8",
    )
    run = SelfEvolutionReviewRun(
        id=run_id,
        mode="fork_reviewer",
        status="running",
        approval_mode=approval_mode,
        artifacts=artifacts,
        policy=policy,
    )
    engine.store.save_self_evolution_review_run(run)
    return run


def _render_fork_reviewer_task(input_payload: dict, policy: dict) -> str:
    counts = input_payload.get("counts", {})
    return "\n".join([
        "# Fork Reviewer Task",
        "",
        "## Objective",
        "",
        (
            "Review self-evolution evidence and candidate skills, then prepare "
            "only the allowed approval requests and review artifacts."
        ),
        "",
        "## Current Inputs",
        "",
        f"- approval_mode: {input_payload.get('approval_mode')}",
        f"- evidence_count: {counts.get('evidence', 0)}",
        f"- proposal_count: {counts.get('proposals', 0)}",
        f"- approval_request_count: {counts.get('approval_requests', 0)}",
        f"- skill_usage_count: {counts.get('skill_usage', 0)}",
        "",
        "## Policy",
        "",
        f"- can_record_evidence: {str(policy.get('can_record_evidence')).lower()}",
        f"- can_generate_candidate: {str(policy.get('can_generate_candidate')).lower()}",
        f"- can_generate_eval_case: {str(policy.get('can_generate_eval_case')).lower()}",
        f"- can_run_eval: {str(policy.get('can_run_eval')).lower()}",
        f"- can_submit_approval_request: {str(policy.get('can_submit_approval_request')).lower()}",
        f"- can_approve: {str(policy.get('can_approve')).lower()}",
        f"- can_promote: {str(policy.get('can_promote')).lower()}",
        f"- project_write: {policy.get('project_write')}",
        f"- network: {policy.get('network')}",
        "",
        "## Required Output",
        "",
        "- Write structured output.json and report.md through the review runner.",
        "- Do not approve, promote, or edit project skills directly.",
        "- Leave every candidate behind an eval, execution eval, and approval gate.",
        "",
    ])


def _finish_fork_reviewer_run(
    engine: EvolutionEngine,
    run: SelfEvolutionReviewRun,
    result: dict,
    *,
    status: str | None = None,
    error: str = "",
) -> SelfEvolutionReviewRun:
    summary = _review_run_summary(engine, result)
    run.status = status or str(result.get("status", "idle"))
    run.summary = summary
    run.completed_at = time.time()
    run.error = error
    (engine.project_root / run.artifacts["output"]).write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    (engine.project_root / run.artifacts["report"]).write_text(
        _render_fork_reviewer_report(run),
        encoding="utf-8",
    )
    engine.store.update_self_evolution_review_run(run)
    return run


def _review_run_summary(engine: EvolutionEngine, result: dict) -> dict:
    request_ids = [
        request.proposal_id for request in result.get("requests", [])
    ]
    auto_promotion_ids = [
        str(item.get("proposal_id", ""))
        for item in result.get("auto_promotions", [])
        if item.get("proposal_id")
    ]
    evidence_ids = request_ids + [
        proposal_id
        for proposal_id in auto_promotion_ids
        if proposal_id not in request_ids
    ]
    return {
        "status": result.get("status", "idle"),
        "requests": request_ids,
        "auto_promotions": list(result.get("auto_promotions", [])),
        "auto_quarantines": list(result.get("auto_quarantines", [])),
        "request_evidence": {
            proposal_id: _proposal_evidence_details(engine, proposal_id)
            for proposal_id in evidence_ids
        },
        "skipped": list(result.get("skipped", [])),
        "generated_candidates": list(result.get("generated_candidates", [])),
        "generated_eval_cases": list(result.get("generated_eval_cases", [])),
        "generated_evaluations": list(result.get("generated_evaluations", [])),
        "generated_execution_evals": list(
            result.get("generated_execution_evals", [])
        ),
        "blocked_generated_candidates": list(
            result.get("blocked_generated_candidates", [])
        ),
        "ingested_usage": list(result.get("ingested_usage", [])),
    }


def _proposal_evidence_details(
    engine: EvolutionEngine,
    proposal_id: str,
) -> list[dict]:
    proposal = engine.store.get_proposal(proposal_id)
    if proposal is None:
        return []
    details: list[dict] = []
    for evidence_id in proposal.evidence_ids:
        evidence = engine.store.get_evidence(evidence_id)
        if evidence is None:
            continue
        details.append({
            "id": evidence.id,
            "kind": evidence.kind,
            "source": evidence.source,
            "summary": evidence.summary,
            "metadata": evidence.metadata,
        })
    return details


def _render_fork_reviewer_report(run: SelfEvolutionReviewRun) -> str:
    summary = run.summary
    lines = [
        "# Self-Evolution Fork Reviewer Run",
        "",
        f"- Run ID: `{run.id}`",
        f"- Mode: `{run.mode}`",
        f"- Status: `{run.status}`",
        f"- Approval mode: `{run.approval_mode}`",
        f"- Can approve: `{run.policy.get('can_approve')}`",
        f"- Can promote: `{run.policy.get('can_promote')}`",
        f"- Project write: `{run.policy.get('project_write')}`",
        f"- Requests: `{len(summary.get('requests', []))}`",
        f"- Auto promotions: `{len(summary.get('auto_promotions', []))}`",
        f"- Auto quarantines: `{len(summary.get('auto_quarantines', []))}`",
        f"- Generated candidates: `{len(summary.get('generated_candidates', []))}`",
        f"- Generated eval case groups: `{len(summary.get('generated_eval_cases', []))}`",
        f"- Ingested usage records: `{len(summary.get('ingested_usage', []))}`",
    ]
    trusted_auto_policy = run.policy.get("trusted_auto_policy", {})
    if run.approval_mode == "trusted-auto" and isinstance(trusted_auto_policy, dict):
        rollback_events = ", ".join(
            str(event) for event in trusted_auto_policy.get("rollback_events", [])
        )
        lines.extend([
            "",
            "## Trusted-Auto Policy",
            "",
            "- Auto promote scope: "
            f"`{trusted_auto_policy.get('auto_promote_scope', '')}`",
            "- Rollback threshold: "
            f"`{trusted_auto_policy.get('rollback_threshold', '')}`",
            f"- Rollback events: `{rollback_events}`",
        ])
    auto_promotions = summary.get("auto_promotions", [])
    if auto_promotions:
        lines.extend(["", "## Auto Promotions", ""])
        for item in auto_promotions:
            lines.append(
                "- "
                f"proposal=`{item.get('proposal_id')}` "
                f"request=`{item.get('request_id')}` "
                f"ok=`{item.get('ok')}` "
                f"message={item.get('message')}"
            )
    auto_quarantines = summary.get("auto_quarantines", [])
    if auto_quarantines:
        lines.extend(["", "## Auto Quarantines", ""])
        for item in auto_quarantines:
            lines.append(
                "- "
                f"skill=`{item.get('skill_name')}` "
                f"ok=`{item.get('ok')}` "
                f"path=`{item.get('path')}` "
                f"reason={item.get('reason')}"
            )
    generated_eval_cases = summary.get("generated_eval_cases", [])
    if generated_eval_cases:
        lines.extend(["", "## Generated Eval Cases", ""])
        for item in generated_eval_cases:
            case_ids = [
                str(case_id).strip()
                for case_id in item.get("case_ids", [])
                if str(case_id).strip()
            ]
            cases = ", ".join(f"`{case_id}`" for case_id in case_ids)
            lines.append(
                "- "
                f"proposal=`{item.get('proposal_id')}` "
                f"skill=`{item.get('skill_name')}` "
                f"eval_cases=`{len(case_ids)}` "
                f"cases={cases}"
            )
    generated_execution_evals = summary.get("generated_execution_evals", [])
    if generated_execution_evals:
        lines.extend(["", "## Generated Execution Evals", ""])
        for item in generated_execution_evals:
            canary = item.get("canary_summary", {})
            lines.append(
                "- "
                f"proposal=`{item.get('proposal_id')}` "
                f"skill=`{item.get('skill_name')}` "
                f"ok=`{item.get('ok')}` "
                f"runner=`{canary.get('runner', '')}` "
                f"rounds=`{canary.get('rounds', '')}` "
                f"canary_mode=`{canary.get('mode', '')}` "
                f"canary_injections=`{canary.get('injections', 0)}` "
                f"message={item.get('message')}"
            )
    blocked_generated_candidates = summary.get("blocked_generated_candidates", [])
    if blocked_generated_candidates:
        lines.extend(["", "## Blocked Generated Candidates", ""])
        for item in blocked_generated_candidates:
            canary = item.get("canary_summary", {})
            lines.append(
                "- "
                f"proposal=`{item.get('proposal_id')}` "
                f"skill=`{item.get('skill_name')}` "
                f"blocked=`{item.get('blocked')}` "
                f"runner=`{canary.get('runner', '')}` "
                f"rounds=`{canary.get('rounds', '')}` "
                f"reason={item.get('reason')}"
            )
    if run.error:
        lines.append(f"- Error: `{run.error}`")
    evidence_by_request = summary.get("request_evidence", {})
    if evidence_by_request:
        lines.extend(["", "## Request Evidence", ""])
        for proposal_id, evidences in evidence_by_request.items():
            lines.append(f"### Proposal `{proposal_id}`")
            if not evidences:
                lines.append("- No linked evidence was recorded.")
                continue
            for evidence in evidences:
                lines.append(
                    "- "
                    f"`{evidence.get('id')}` "
                    f"kind=`{evidence.get('kind')}` "
                    f"source=`{evidence.get('source')}` "
                    f"summary={evidence.get('summary')}"
                )
                metadata = evidence.get("metadata", {})
                summaries = metadata.get("summaries", []) if isinstance(metadata, dict) else []
                for item in summaries:
                    lines.append(f"  - usage summary: {item}")
    return "\n".join(lines) + "\n"


def _project_relative(engine: EvolutionEngine, path: Path) -> str:
    return str(path.relative_to(engine.project_root))


def _auto_quarantine_trusted_auto_skills(
    engine: EvolutionEngine,
    *,
    approval_mode: str,
    threshold: int,
    rollback_events: list[str] | None,
) -> list[dict]:
    if approval_mode != "trusted-auto":
        return []
    threshold = max(1, int(threshold))
    event_filter = {
        str(event).strip()
        for event in (rollback_events or ["failure", "user_feedback"])
        if str(event).strip()
    }
    if not event_filter:
        event_filter = {"failure", "user_feedback"}

    results: list[dict] = []
    usage = engine.load_skill_usage()
    for request in engine.store.load_skill_approval_requests():
        if request.approval_mode != "trusted-auto":
            continue
        if request.status != "approved" or request.resolved_at <= 0:
            continue
        skill_name = request.skill_name
        if not engine.has_project_skill(skill_name):
            continue
        post_approval_usage = _usage_records_after_approval(usage, request)
        negative = [
            record
            for record in post_approval_usage
            if str(record.get("skill_name", "")).strip() == skill_name
            and str(record.get("event", "")).strip() in event_filter
        ]
        if len(negative) < threshold:
            continue
        reason = _trusted_auto_quarantine_reason(request, negative)
        ok, path = engine.quarantine_skill(
            skill_name,
            reason=reason,
            source="trusted-auto-rollback",
        )
        results.append({
            "skill_name": skill_name,
            "request_id": request.id,
            "ok": ok,
            "path": path if ok else "",
            "reason": reason,
            "negative_events": len(negative),
            "message": path,
        })
    return results


def _usage_records_after_approval(usage: list[dict], request) -> list[dict]:
    baseline_count = getattr(request, "usage_baseline_count", None)
    if baseline_count is not None:
        try:
            cursor = max(0, int(baseline_count))
        except (TypeError, ValueError):
            cursor = len(usage)
        return usage[min(cursor, len(usage)) :]

    return [
        record
        for record in usage
        if float(record.get("created_at", 0.0) or 0.0) > request.resolved_at
    ]


def _trusted_auto_managed_skill_names(engine: EvolutionEngine) -> set[str]:
    names: set[str] = set()
    for request in engine.store.load_skill_approval_requests():
        if request.approval_mode != "trusted-auto":
            continue
        if request.status != "approved" or request.resolved_at <= 0:
            continue
        if engine.has_project_skill(request.skill_name):
            names.add(request.skill_name)
    return names


def _trusted_auto_quarantine_reason(request, negative: list[dict]) -> str:
    summaries = []
    for record in negative:
        metadata = record.get("metadata", {})
        if isinstance(metadata, dict):
            summary = str(metadata.get("summary", "")).strip()
            if summary:
                summaries.append(summary)
    detail = "; ".join(summaries[:3]) or "negative usage after trusted-auto promotion"
    return (
        "trusted-auto rollback: "
        f"skill '{request.skill_name}' had {len(negative)} negative usage "
        f"event(s) after approval {request.id}: {detail}"
    )


def _ingest_evidence_as_skill_usage(engine: EvolutionEngine) -> list[str]:
    ingested: list[str] = []
    seen_evidence_ids = _skill_usage_evidence_ids(engine)
    for evidence in engine.store.load_evidence():
        if evidence.kind not in {"failure", "user_feedback"}:
            continue
        if evidence.source == "skill-usage":
            continue
        if evidence.id in seen_evidence_ids:
            continue
        skill_name = _evidence_skill_name(evidence.metadata)
        if not skill_name or not engine.has_project_skill(skill_name):
            continue
        summary = _evidence_summary(evidence.summary, evidence.metadata)
        record = engine.record_skill_usage(
            skill_name,
            event=evidence.kind,
            source=f"evidence:{evidence.source}",
            metadata={
                "summary": summary,
                "evidence_id": evidence.id,
                "evidence_source": evidence.source,
            },
        )
        seen_evidence_ids.add(evidence.id)
        ingested.append(str(record["id"]))
    return ingested


def _skill_usage_evidence_ids(engine: EvolutionEngine) -> set[str]:
    evidence_ids: set[str] = set()
    for record in engine.load_skill_usage():
        metadata = record.get("metadata", {})
        if isinstance(metadata, dict):
            evidence_id = str(metadata.get("evidence_id", "")).strip()
            if evidence_id:
                evidence_ids.add(evidence_id)
    return evidence_ids


def _evidence_skill_name(metadata: dict) -> str:
    if not isinstance(metadata, dict):
        return ""
    return str(metadata.get("skill_name") or metadata.get("skill") or "").strip()


def _evidence_summary(summary: str, metadata: dict) -> str:
    if isinstance(metadata, dict):
        metadata_summary = str(metadata.get("summary", "")).strip()
        if metadata_summary:
            return metadata_summary
    return summary.strip()


def _generate_usage_patch_candidates(
    engine: EvolutionEngine,
    *,
    suppressed_skills: set[str] | None = None,
) -> tuple[list[str], list[dict]]:
    generated: list[str] = []
    reviews: list[dict] = []
    suppressed = suppressed_skills or set()
    for suggestion in engine.suggest_quarantine(failure_threshold=2):
        skill_name = str(suggestion.get("skill_name", "")).strip()
        if skill_name in suppressed:
            continue
        if not skill_name or _has_open_patch_candidate(engine, skill_name):
            continue
        try:
            proposal = engine.propose_skill_patch_from_usage(
                skill_name,
                failure_threshold=2,
            )
        except ValueError:
            continue
        generated.append(proposal.id)
        reviews.append(engine.review_eval_case_suggestions(proposal.id))
    return generated, reviews


def _has_open_patch_candidate(engine: EvolutionEngine, skill_name: str) -> bool:
    for proposal in engine.store.load_proposals():
        if proposal.target != "skill" or proposal.status != "proposed":
            continue
        try:
            payload = json.loads(proposal.change)
        except json.JSONDecodeError:
            continue
        if payload.get("action") == "patch" and payload.get("name") == skill_name:
            return True
    return False


def _materialize_safe_eval_suggestions(
    engine: EvolutionEngine,
    reviews: list[dict],
) -> list[dict]:
    generated: list[dict] = []
    for review in reviews:
        suggestions = list(review.get("suggestions", []))
        if not _review_is_safe_to_materialize(review, suggestions):
            continue
        case_ids = []
        for suggestion in suggestions:
            case_ids.append(
                engine.add_eval_case(
                    str(review["proposal_id"]),
                    task=str(suggestion["task"]),
                    must_contain=list(suggestion["must_contain"]),
                    must_not_contain=list(suggestion.get("must_not_contain", [])),
                )
            )
        generated.append({
            "proposal_id": review["proposal_id"],
            "skill_name": review["skill_name"],
            "case_ids": case_ids,
        })
    return generated


def _review_is_safe_to_materialize(
    review: dict,
    suggestions: list[dict],
) -> bool:
    if review.get("warnings"):
        return False
    if review.get("uncovered_usage_feedback"):
        return False
    if len(suggestions) < MIN_EXECUTION_EVAL_CASES:
        return False
    for suggestion in suggestions:
        if suggestion.get("quality") == "low":
            return False
        if not suggestion.get("must_contain"):
            return False
        if not str(suggestion.get("task", "")).strip():
            return False
    return True


def _evaluate_generated_candidates(
    engine: EvolutionEngine,
    generated_eval_cases: list[dict],
) -> list[dict]:
    evaluations: list[dict] = []
    for generated in generated_eval_cases:
        proposal_id = str(generated["proposal_id"])
        ok, message = engine.evaluate(proposal_id)
        evaluations.append({
            "proposal_id": proposal_id,
            "skill_name": generated["skill_name"],
            "ok": ok,
            "message": message,
        })
    return evaluations


def _run_execution_evals_for_generated_candidates(
    engine: EvolutionEngine,
    generated_evaluations: list[dict],
) -> list[dict]:
    execution_evals: list[dict] = []
    for evaluation in generated_evaluations:
        if not evaluation.get("ok"):
            continue
        proposal_id = str(evaluation["proposal_id"])
        ok, message = engine.run_execution_eval(proposal_id)
        execution_evals.append({
            "proposal_id": proposal_id,
            "skill_name": evaluation["skill_name"],
            "ok": ok,
            "message": message,
            "canary_summary": _execution_eval_canary_summary(engine, proposal_id),
        })
    return execution_evals


def _execution_eval_canary_summary(
    engine: EvolutionEngine,
    proposal_id: str,
) -> dict:
    report_path = engine.execution_eval_report_path(proposal_id)
    if not report_path.exists():
        return {}
    try:
        report = json.loads(report_path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return {}
    rounds = list(report.get("rounds", [])) if isinstance(report, dict) else []
    summary = report.get("summary", {}) if isinstance(report, dict) else {}
    passed = int(summary.get("passed", 0) or 0)
    total = int(summary.get("total", len(rounds)) or 0)
    canary_paths = [
        str(round_.get("artifacts", {}).get("canary_skill", "")).strip()
        for round_ in rounds
        if str(round_.get("artifacts", {}).get("canary_skill", "")).strip()
    ]
    return {
        "runner": str(report.get("runner", "")),
        "rounds": f"{passed}/{total}",
        "mode": "candidate_canary" if canary_paths else "",
        "injections": len(canary_paths),
        "first_canary_skill": canary_paths[0] if canary_paths else "",
    }


def _block_failed_generated_candidates(
    engine: EvolutionEngine,
    generated_execution_evals: list[dict],
) -> list[dict]:
    blocked: list[dict] = []
    for execution_eval in generated_execution_evals:
        if execution_eval.get("ok"):
            continue
        proposal_id = str(execution_eval.get("proposal_id", "")).strip()
        if not proposal_id:
            continue
        proposal = engine.store.get_proposal(proposal_id)
        if proposal is None:
            continue
        reason = _generated_candidate_block_reason(execution_eval)
        engine._mark_candidate_approval_blocked(proposal, reason)
        blocked.append({
            "proposal_id": proposal_id,
            "skill_name": execution_eval.get("skill_name", ""),
            "reason": reason,
            "blocked": True,
            "canary_summary": dict(execution_eval.get("canary_summary", {})),
        })
    return blocked


def _generated_candidate_block_reason(execution_eval: dict) -> str:
    canary = execution_eval.get("canary_summary", {})
    rounds = str(canary.get("rounds", "0/0")).strip() or "0/0"
    runner = str(canary.get("runner", "")).strip() or "unknown"
    message = str(execution_eval.get("message", "")).strip()
    suffix = f": {message}" if message else ""
    return (
        f"generated candidate canary failed: {rounds} rounds passed "
        f"(runner={runner}){suffix}"
    )


def _submit_generated_approval_requests(
    engine: EvolutionEngine,
    generated_execution_evals: list[dict],
    *,
    approval_mode: str,
) -> tuple[list[Any], list[dict], list[dict]]:
    requests: list[Any] = []
    skipped: list[dict] = []
    auto_promotions: list[dict] = []
    for execution_eval in generated_execution_evals:
        if not execution_eval.get("ok"):
            continue
        proposal_id = str(execution_eval["proposal_id"])
        try:
            request = engine.submit_skill_approval_request(
                proposal_id,
                approval_mode=approval_mode,
                source="self-evolution-generated-candidate",
            )
        except ValueError as exc:
            skipped.append({"proposal_id": proposal_id, "reason": str(exc)})
            continue
        if approval_mode == "trusted-auto":
            ok, message = engine.resolve_skill_approval_request(
                request.id,
                approved=True,
                reviewer="self-evolution-policy",
                reason=(
                    "trusted-auto: generated candidate passed deterministic "
                    "eval and execution eval"
                ),
            )
            auto_promotions.append({
                "proposal_id": proposal_id,
                "skill_name": execution_eval["skill_name"],
                "request_id": request.id,
                "ok": ok,
                "message": message,
            })
            if not ok:
                skipped.append({"proposal_id": proposal_id, "reason": message})
            continue
        requests.append(request)
    return requests, skipped, auto_promotions

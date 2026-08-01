"""Automatic review pass for self-evolution candidates."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any

from mewcode.evolution.engine import EvolutionEngine, MIN_EXECUTION_EVAL_CASES
from mewcode.evolution.models import SelfEvolutionReviewRun, new_evolution_id


def format_review_notification(result: dict) -> str:
    requests = result.get("requests", [])
    if not requests:
        return ""
    lines = ["Self-evolution approval request(s) ready:"]
    for request in requests:
        lines.append(
            f"- {request.proposal_id} / {request.skill_name} "
            f"mode={request.approval_mode} report={request.eval_report_markdown}"
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

    approval_mode = getattr(self_evolution_config, "skill_approval_mode", "manual")
    engine = EvolutionEngine(project_root)
    review_run = _start_fork_reviewer_run(engine, approval_mode=approval_mode)
    try:
        result = _review_enabled_skill_candidates(
            engine,
            approval_mode=approval_mode,
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

    result["review_run"] = _finish_fork_reviewer_run(engine, review_run, result)
    return result


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
        "ingested_usage": [],
        "review_run": None,
    }


def _review_enabled_skill_candidates(
    engine: EvolutionEngine,
    *,
    approval_mode: str,
) -> dict:
    requests = []
    skipped: list[dict] = []
    ingested_usage = _ingest_evidence_as_skill_usage(engine)

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
        _generate_usage_patch_candidates(engine)
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
    generated_requests, generated_request_skips = (
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
        "ingested_usage": ingested_usage,
        "review_run": None,
    }


def _start_fork_reviewer_run(
    engine: EvolutionEngine,
    *,
    approval_mode: str,
) -> SelfEvolutionReviewRun:
    run_id = new_evolution_id("review")
    run_dir = engine.project_root / ".mewcode" / "evolution" / "review_runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=False)
    artifacts = {
        "input": _project_relative(engine, run_dir / "input.json"),
        "policy": _project_relative(engine, run_dir / "policy.json"),
        "output": _project_relative(engine, run_dir / "output.json"),
        "report": _project_relative(engine, run_dir / "report.md"),
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
    }
    input_payload = {
        "approval_mode": approval_mode,
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


def _finish_fork_reviewer_run(
    engine: EvolutionEngine,
    run: SelfEvolutionReviewRun,
    result: dict,
    *,
    status: str | None = None,
    error: str = "",
) -> SelfEvolutionReviewRun:
    summary = _review_run_summary(result)
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


def _review_run_summary(result: dict) -> dict:
    return {
        "status": result.get("status", "idle"),
        "requests": [
            request.proposal_id for request in result.get("requests", [])
        ],
        "skipped": list(result.get("skipped", [])),
        "generated_candidates": list(result.get("generated_candidates", [])),
        "generated_eval_cases": list(result.get("generated_eval_cases", [])),
        "generated_evaluations": list(result.get("generated_evaluations", [])),
        "generated_execution_evals": list(
            result.get("generated_execution_evals", [])
        ),
        "ingested_usage": list(result.get("ingested_usage", [])),
    }


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
        f"- Generated candidates: `{len(summary.get('generated_candidates', []))}`",
        f"- Generated eval case groups: `{len(summary.get('generated_eval_cases', []))}`",
        f"- Ingested usage records: `{len(summary.get('ingested_usage', []))}`",
    ]
    if run.error:
        lines.append(f"- Error: `{run.error}`")
    return "\n".join(lines) + "\n"


def _project_relative(engine: EvolutionEngine, path: Path) -> str:
    return str(path.relative_to(engine.project_root))


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
) -> tuple[list[str], list[dict]]:
    generated: list[str] = []
    reviews: list[dict] = []
    for suggestion in engine.suggest_quarantine(failure_threshold=2):
        skill_name = str(suggestion.get("skill_name", "")).strip()
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
        })
    return execution_evals


def _submit_generated_approval_requests(
    engine: EvolutionEngine,
    generated_execution_evals: list[dict],
    *,
    approval_mode: str,
) -> tuple[list[Any], list[dict]]:
    requests: list[Any] = []
    skipped: list[dict] = []
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
        requests.append(request)
    return requests, skipped

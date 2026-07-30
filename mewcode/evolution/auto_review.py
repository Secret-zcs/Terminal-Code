"""Automatic review pass for self-evolution candidates."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from mewcode.evolution.engine import EvolutionEngine


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
        return {"status": "disabled", "requests": [], "skipped": []}

    approval_mode = getattr(self_evolution_config, "skill_approval_mode", "manual")
    engine = EvolutionEngine(project_root)
    requests = []
    skipped: list[dict] = []

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

    return {
        "status": "submitted" if requests else "idle",
        "requests": requests,
        "skipped": skipped,
    }

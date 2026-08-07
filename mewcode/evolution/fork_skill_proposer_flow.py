"""Coordinator that turns fork proposer output into sandboxed candidates."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from mewcode.client import LLMClient
from mewcode.evolution.engine import EvolutionEngine
from mewcode.evolution.fork_skill_proposer_agent import (
    ForkSkillProposerAgent,
    classify_fork_skill_proposer_error,
)


MAX_FORK_SKILL_PROPOSALS_PER_RUN = 3


async def run_fork_skill_proposer_for_usage(
    client: LLMClient,
    project_root: str | Path,
    *,
    review_run_id: str,
    external_samples: list[str] | None = None,
    max_proposals: int = MAX_FORK_SKILL_PROPOSALS_PER_RUN,
) -> dict[str, Any]:
    """Generate candidate patches for skills with repeated negative usage."""
    engine = EvolutionEngine(project_root)
    review_run = _load_running_review_run(engine, review_run_id)
    tasks = _collect_usage_patch_tasks(engine)[: max(0, int(max_proposals))]
    generated: list[str] = []
    failures: list[dict[str, Any]] = []
    outputs: list[dict[str, Any]] = []
    agent = ForkSkillProposerAgent(client)

    for task in tasks:
        skill_name = task["skill_name"]
        try:
            candidate = await agent.propose(
                original_skill=task["original_skill"],
                evidence_summary=task["evidence_summary"],
                task_markdown=task["task_markdown"],
                external_samples=external_samples,
            )
            _validate_patch_scope(candidate, task)
            evidence = engine.record_evidence(
                f"Fork Skill Proposer generated a candidate patch for '{skill_name}'.",
                kind="user_feedback",
                source="fork-skill-proposer",
                metadata={
                    "skill": skill_name,
                    "summaries": task["summaries"],
                    "events": task["events"],
                    "review_run_id": review_run.id,
                },
            )
            proposal = engine.propose_skill_patch(
                name=skill_name,
                description=candidate["description"],
                body=candidate["body"],
                allowed_tools=candidate["allowedTools"],
                mode=candidate["mode"],
                context=candidate["context"],
                rationale=candidate["rationale"],
                evidence_ids=[evidence.id],
            )
            validation = engine.validate(proposal)
            if not validation.ok:
                engine.reject(proposal.id)
                raise ValueError("; ".join(validation.errors))
            engine.record_candidate_generation_metadata(
                proposal.id,
                source="fork-skill-proposer",
                review_run_id=review_run.id,
                proposer_output=candidate,
            )
            generated.append(proposal.id)
            outputs.append({
                "proposal_id": proposal.id,
                "skill_name": skill_name,
                "candidate": candidate,
            })
        except Exception as exc:
            failures.append({
                "skill_name": skill_name,
                "error_type": type(exc).__name__,
                "error_category": classify_fork_skill_proposer_error(exc),
                "attempts": int(getattr(exc, "attempts", 1) or 1),
                "retry_reasons": list(
                    getattr(exc, "retry_reasons", []) or []
                ),
                "error": str(exc)[:4000],
            })

    result = {
        "status": "generated" if generated else "failed" if failures else "idle",
        "generated_candidates": generated,
        "failures": failures,
        "outputs": outputs,
    }
    _persist_flow_audit(engine, review_run, result)
    return result


def _collect_usage_patch_tasks(engine: EvolutionEngine) -> list[dict[str, Any]]:
    tasks: list[dict[str, Any]] = []
    for suggestion in engine.suggest_quarantine(failure_threshold=2):
        skill_name = str(suggestion.get("skill_name", "")).strip()
        if not skill_name or _has_open_patch_candidate(engine, skill_name):
            continue
        existing = engine._load_existing_project_skill(skill_name)
        if existing is None or existing.source_path is None:
            continue
        summaries = [
            str(summary).strip()
            for summary in suggestion.get("summaries", [])
            if str(summary).strip()
        ]
        events = [
            str(event).strip()
            for event in suggestion.get("events", [])
            if str(event).strip()
        ]
        evidence_summary = json.dumps(
            {
                "skill_name": skill_name,
                "negative_events": suggestion.get("negative_events", 0),
                "events": events,
                "feedback_summaries": summaries,
            },
            ensure_ascii=False,
            indent=2,
        )
        tasks.append({
            "skill_name": skill_name,
            "original_skill": existing.source_path.read_text(encoding="utf-8"),
            "summaries": summaries,
            "events": events,
            "allowed_tools": list(existing.allowed_tools),
            "mode": existing.mode,
            "context": existing.context,
            "evidence_summary": evidence_summary,
            "task_markdown": (
                f"Patch the existing project Skill '{skill_name}' to address every "
                "listed feedback summary. Keep the scope narrow, preserve its "
                "execution mode, context, and tool permissions, and include the "
                "observed feedback text so deterministic eval cases can verify it."
            ),
        })
    return tasks


def _validate_patch_scope(candidate: dict[str, Any], task: dict[str, Any]) -> None:
    if candidate.get("action") != "patch":
        raise ValueError("fork proposer must return action=patch for an existing skill")
    if candidate.get("name") != task["skill_name"]:
        raise ValueError("fork proposer candidate name does not match the target skill")
    if candidate.get("mode") != task["mode"]:
        raise ValueError("fork proposer cannot change the existing skill mode")
    if candidate.get("context") != task["context"]:
        raise ValueError("fork proposer cannot change the existing skill context")
    if candidate.get("allowedTools") != task["allowed_tools"]:
        raise ValueError("fork proposer cannot change existing skill tool permissions")


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


def _load_running_review_run(engine: EvolutionEngine, review_run_id: str):
    clean_id = review_run_id.strip()
    for run in reversed(engine.store.load_self_evolution_review_runs()):
        if run.id == clean_id and run.mode == "fork_reviewer":
            if run.status != "running":
                raise ValueError(f"review run {clean_id} is not running")
            return run
    raise ValueError(f"review run {clean_id} not found")


def _persist_flow_audit(engine: EvolutionEngine, review_run, result: dict) -> None:
    run_dir = (
        engine.project_root
        / ".mewcode"
        / "evolution"
        / "review_runs"
        / review_run.id
    ).resolve()
    root = engine.project_root.resolve()
    if not run_dir.is_relative_to(root) or not run_dir.is_dir():
        raise ValueError("fork proposer review directory escapes project root")
    json_path = run_dir / "skill_proposer.json"
    markdown_path = run_dir / "skill_proposer.md"
    json_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    lines = [
        "## Fork Skill Proposer",
        "",
        f"- Status: `{result['status']}`",
        f"- Generated candidates: `{len(result['generated_candidates'])}`",
        f"- Failures: `{len(result['failures'])}`",
        "- Authority: candidate generation only; cannot approve or promote",
        "",
    ]
    for item in result["outputs"]:
        lines.append(
            f"- proposal=`{item['proposal_id']}` skill=`{item['skill_name']}`"
        )
    for item in result["failures"]:
        lines.append(
            f"- failed skill=`{item['skill_name']}` error={item['error']}"
        )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    review_run.artifacts["skill_proposer_json"] = str(json_path.relative_to(root))
    review_run.artifacts["skill_proposer"] = str(markdown_path.relative_to(root))
    engine.store.update_self_evolution_review_run(review_run)

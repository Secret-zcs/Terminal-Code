from __future__ import annotations

import json
from pathlib import Path

import pytest

from mewcode.config import SelfEvolutionConfig
from mewcode.evolution.engine import EvolutionEngine
from mewcode.tools.base import StreamEnd, TextDelta


def _seed_failing_skill(tmp_path: Path) -> tuple[EvolutionEngine, Path, str]:
    skill_path = tmp_path / ".mewcode" / "skills" / "review-loop" / "SKILL.md"
    skill_path.parent.mkdir(parents=True)
    original = (
        "---\n"
        "name: review-loop\n"
        "description: Review flow\n"
        "mode: inline\n"
        "context: recent\n"
        "---\n\n"
        "# Review\n\nOriginal workflow.\n"
    )
    skill_path.write_text(original, encoding="utf-8")
    engine = EvolutionEngine(tmp_path)
    engine.record_skill_usage(
        "review-loop",
        event="failure",
        source="conversation-1",
        metadata={"summary": "错误地跳过复盘文档。"},
    )
    engine.record_skill_usage(
        "review-loop",
        event="user_feedback",
        source="conversation-2",
        metadata={"summary": "用户纠正：遗漏验证。"},
    )
    return engine, skill_path, original


def _model_candidate() -> dict:
    return {
        "schema_version": 1,
        "action": "patch",
        "name": "review-loop",
        "description": "Review flow with explicit verification.",
        "mode": "inline",
        "context": "recent",
        "allowedTools": [],
        "body": (
            "# Review\n\n"
            "## Usage Feedback Patch Notes\n\n"
            "- 错误地跳过复盘文档。\n"
            "- 用户纠正：遗漏验证。\n\n"
            "## Required Patch Behavior\n\n"
            "Read the target, update the recap, run verification, and report evidence."
        ),
        "rationale": "Two real conversations exposed the same missing verification step.",
    }


@pytest.mark.asyncio
async def test_fork_proposer_generates_sandbox_candidate_then_existing_gates_run(
    tmp_path: Path,
) -> None:
    from mewcode.evolution.auto_review import (
        complete_fork_reviewer_run,
        start_fork_reviewer_run,
    )
    from mewcode.evolution.fork_skill_proposer_flow import (
        run_fork_skill_proposer_for_usage,
    )

    engine, skill_path, original = _seed_failing_skill(tmp_path)
    calls: list[dict] = []

    class FakeClient:
        async def stream(self, conversation, system="", tools=None):
            calls.append({
                "messages": conversation.get_messages(),
                "system": system,
                "tools": tools,
            })
            yield TextDelta(json.dumps(_model_candidate(), ensure_ascii=False))
            yield StreamEnd("end_turn", input_tokens=120, output_tokens=90)

    config = SelfEvolutionConfig(enabled=True, skill_approval_mode="manual")
    started = start_fork_reviewer_run(tmp_path, config)
    review_run_id = started["review_run"].id
    proposal_result = await run_fork_skill_proposer_for_usage(
        FakeClient(),
        tmp_path,
        review_run_id=review_run_id,
    )

    assert len(calls) == 1
    assert calls[0]["tools"] == []
    assert proposal_result["status"] == "generated"
    assert len(proposal_result["generated_candidates"]) == 1
    proposal_id = proposal_result["generated_candidates"][0]
    candidate = engine.candidate_skill_path(proposal_id).read_text(encoding="utf-8")
    assert "Read the target, update the recap" in candidate
    assert skill_path.read_text(encoding="utf-8") == original
    manifest = json.loads(
        engine.candidate_manifest_path(proposal_id).read_text(encoding="utf-8")
    )
    assert manifest["generation_source"] == "fork-skill-proposer"
    assert manifest["proposer_usage"] == {
        "input_tokens": 120,
        "output_tokens": 90,
    }
    assert (
        tmp_path
        / ".mewcode"
        / "evolution"
        / "review_runs"
        / review_run_id
        / "skill_proposer.json"
    ).is_file()

    completed = complete_fork_reviewer_run(tmp_path, review_run_id, config)

    assert completed["generated_candidates"] == [proposal_id]
    assert not any(
        item.get("proposal_id") == proposal_id for item in completed["skipped"]
    )
    assert len(completed["generated_eval_cases"]) == 1
    assert len(completed["generated_eval_cases"][0]["case_ids"]) == 3
    assert completed["generated_execution_evals"][0]["ok"] is True
    assert len(completed["requests"]) == 1
    assert completed["requests"][0].proposal_id == proposal_id
    assert skill_path.read_text(encoding="utf-8") == original


@pytest.mark.asyncio
async def test_invalid_fork_proposer_output_falls_back_without_model_candidate(
    tmp_path: Path,
) -> None:
    from mewcode.evolution.auto_review import (
        complete_fork_reviewer_run,
        start_fork_reviewer_run,
    )
    from mewcode.evolution.fork_skill_proposer_flow import (
        run_fork_skill_proposer_for_usage,
    )

    engine, skill_path, original = _seed_failing_skill(tmp_path)

    class InvalidClient:
        async def stream(self, conversation, system="", tools=None):
            payload = _model_candidate()
            payload["name"] = "different-skill"
            yield TextDelta(json.dumps(payload, ensure_ascii=False))
            yield StreamEnd("end_turn")

    config = SelfEvolutionConfig(enabled=True, skill_approval_mode="manual")
    started = start_fork_reviewer_run(tmp_path, config)
    review_run_id = started["review_run"].id
    proposal_result = await run_fork_skill_proposer_for_usage(
        InvalidClient(),
        tmp_path,
        review_run_id=review_run_id,
    )

    assert proposal_result["status"] == "failed"
    assert proposal_result["generated_candidates"] == []
    assert engine.store.load_proposals() == []
    assert skill_path.read_text(encoding="utf-8") == original

    completed = complete_fork_reviewer_run(tmp_path, review_run_id, config)

    assert len(completed["generated_candidates"]) == 1
    fallback_id = completed["generated_candidates"][0]
    fallback_manifest = json.loads(
        engine.candidate_manifest_path(fallback_id).read_text(encoding="utf-8")
    )
    assert fallback_manifest.get("generation_source", "deterministic-usage-patch") == (
        "deterministic-usage-patch"
    )
    assert skill_path.read_text(encoding="utf-8") == original

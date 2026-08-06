from __future__ import annotations

import json
from pathlib import Path

import pytest

from mewcode.tools.base import StreamEnd, TextDelta


def _candidate_body() -> str:
    return (
        "阅读相关文件。记录用户反馈。回归测试。验证报告。"
        "不得省略验证，也不得无边界重试。"
    )


@pytest.mark.asyncio
async def test_proposer_benchmark_runs_real_candidate_through_all_gates(
    tmp_path: Path,
) -> None:
    from mewcode.evolution.proposer_benchmark import run_proposer_benchmark

    class FakeClient:
        async def stream(self, conversation, system="", tools=None):
            assert tools == []
            yield TextDelta(json.dumps({
                "schema_version": 1,
                "action": "create",
                "name": "oasst1-follow-up",
                "description": "Handle code follow-up feedback.",
                "mode": "inline",
                "context": "recent",
                "allowedTools": [],
                "body": _candidate_body(),
                "rationale": "Derived from a real multi-turn feedback pattern.",
            }, ensure_ascii=False))
            yield StreamEnd("end_turn", input_tokens=30, output_tokens=40)

    result = await run_proposer_benchmark(
        FakeClient(),
        "benchmarks/oasst1_derived_cases.jsonl",
        max_cases=1,
        workspace_root=tmp_path,
    )

    assert result["summary"] == {
        "total_cases": 1,
        "schema_passed": 1,
        "static_policy_passed": 1,
        "eval_passed": 1,
        "execution_eval_passed": 1,
        "approval_ready": 1,
        "baseline_passed": 0,
    }
    assert result["usage"] == {"input_tokens": 30, "output_tokens": 40}
    assert result["cases"][0]["status"] == "approval-ready"
    assert result["cases"][0]["proposer_attempts"] == 1
    assert result["cases"][0]["execution_eval"]["rounds"] == "3/3"


@pytest.mark.asyncio
async def test_proposer_benchmark_records_schema_failure_without_claiming_gate_pass(
    tmp_path: Path,
) -> None:
    from mewcode.evolution.proposer_benchmark import run_proposer_benchmark

    class InvalidClient:
        async def stream(self, conversation, system="", tools=None):
            yield TextDelta("not json")
            yield StreamEnd("end_turn", input_tokens=4, output_tokens=5)

    result = await run_proposer_benchmark(
        InvalidClient(),
        "benchmarks/oasst1_derived_cases.jsonl",
        max_cases=1,
        workspace_root=tmp_path,
    )

    assert result["summary"]["schema_passed"] == 0
    assert result["summary"]["execution_eval_passed"] == 0
    assert result["summary"]["approval_ready"] == 0
    assert result["cases"][0]["status"] == "schema-failed"
    assert result["cases"][0]["error_type"] == "ForkSkillProposerOutputError"
    assert result["usage"] == {"input_tokens": 8, "output_tokens": 10}
    assert result["cases"][0]["proposer_attempts"] == 2


@pytest.mark.asyncio
async def test_proposer_benchmark_keeps_usage_when_candidate_action_is_invalid(
    tmp_path: Path,
) -> None:
    from mewcode.evolution.proposer_benchmark import run_proposer_benchmark

    class PatchClient:
        async def stream(self, conversation, system="", tools=None):
            yield TextDelta(json.dumps({
                "schema_version": 1,
                "action": "patch",
                "name": "oasst1-follow-up",
                "description": "Handle code follow-up feedback.",
                "mode": "inline",
                "context": "recent",
                "allowedTools": [],
                "body": _candidate_body(),
                "rationale": "Derived from a real multi-turn feedback pattern.",
            }, ensure_ascii=False))
            yield StreamEnd("end_turn", input_tokens=7, output_tokens=9)

    result = await run_proposer_benchmark(
        PatchClient(),
        "benchmarks/oasst1_derived_cases.jsonl",
        max_cases=1,
        workspace_root=tmp_path,
    )

    assert result["usage"] == {"input_tokens": 7, "output_tokens": 9}
    assert result["cases"][0]["proposer_attempts"] == 1
    assert result["cases"][0]["status"] == "schema-failed"

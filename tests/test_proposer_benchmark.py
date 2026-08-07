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
        "provider_failed": 0,
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
    assert result["cases"][0]["error_category"] == "invalid-json"
    assert result["diagnostics"] == {
        "failure_categories": {"invalid-json": 1},
        "retry_reasons": {"invalid-json": 2},
    }


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


@pytest.mark.asyncio
async def test_proposer_benchmark_separates_provider_failure_from_schema_failure(
    tmp_path: Path,
) -> None:
    from mewcode.client import NetworkError
    from mewcode.evolution.proposer_benchmark import run_proposer_benchmark

    class OfflineClient:
        async def stream(self, conversation, system="", tools=None):
            raise NetworkError("connection failed")
            yield

    result = await run_proposer_benchmark(
        OfflineClient(),
        "benchmarks/oasst1_derived_cases.jsonl",
        max_cases=1,
        workspace_root=tmp_path,
    )

    assert result["summary"]["schema_passed"] == 0
    assert result["summary"]["provider_failed"] == 1
    assert result["cases"][0]["status"] == "provider-failed"
    assert result["cases"][0]["error_category"] == "network"


@pytest.mark.asyncio
async def test_proposer_benchmark_can_resume_from_case_offset(tmp_path: Path) -> None:
    from mewcode.evolution.proposer_benchmark import run_proposer_benchmark

    class FakeClient:
        async def stream(self, conversation, system="", tools=None):
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
            yield StreamEnd("end_turn", input_tokens=3, output_tokens=4)

    result = await run_proposer_benchmark(
        FakeClient(),
        "benchmarks/oasst1_derived_cases.jsonl",
        max_cases=1,
        case_offset=1,
        workspace_root=tmp_path,
    )

    assert result["case_offset"] == 1
    assert result["cases"][0]["id"] == "oasst1-9c34cc82c98d"


@pytest.mark.asyncio
async def test_proposer_benchmark_accepts_negated_forbidden_behavior(
    tmp_path: Path,
) -> None:
    from mewcode.evolution.proposer_benchmark import run_proposer_benchmark

    class SafetyClient:
        async def stream(self, conversation, system="", tools=None):
            yield TextDelta(json.dumps({
                "schema_version": 1,
                "action": "create",
                "name": "safe-follow-up",
                "description": "Handle follow-up feedback safely.",
                "mode": "inline",
                "context": "recent",
                "allowedTools": [],
                "body": (
                    "阅读相关文件。记录用户反馈。回归测试。验证报告。"
                    "禁止跳过测试，禁止盲目重试。"
                ),
                "rationale": "Keep verification mandatory.",
            }, ensure_ascii=False))
            yield StreamEnd("end_turn", input_tokens=3, output_tokens=4)

    result = await run_proposer_benchmark(
        SafetyClient(),
        "benchmarks/oasst1_derived_cases.jsonl",
        max_cases=1,
        workspace_root=tmp_path,
    )

    assert result["cases"][0]["status"] == "approval-ready"


@pytest.mark.asyncio
async def test_proposer_benchmark_records_candidate_coverage_before_eval_failure(
    tmp_path: Path,
) -> None:
    from mewcode.evolution.proposer_benchmark import run_proposer_benchmark

    class PartialClient:
        async def stream(self, conversation, system="", tools=None):
            yield TextDelta(json.dumps({
                "schema_version": 1,
                "action": "create",
                "name": "partial-follow-up",
                "description": "Incomplete follow-up flow.",
                "mode": "inline",
                "context": "recent",
                "allowedTools": [],
                "body": "阅读相关文件。记录用户反馈。回归测试。",
                "rationale": "Deliberately omits one required behavior.",
            }, ensure_ascii=False))
            yield StreamEnd("end_turn", input_tokens=3, output_tokens=4)

    result = await run_proposer_benchmark(
        PartialClient(),
        "benchmarks/oasst1_derived_cases.jsonl",
        max_cases=1,
        workspace_root=tmp_path,
    )

    case = result["cases"][0]
    assert case["status"] == "eval-failed"
    assert case["candidate"]["missing_required"] == ["验证报告"]

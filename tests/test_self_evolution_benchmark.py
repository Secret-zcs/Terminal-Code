from __future__ import annotations

import json
from pathlib import Path

from mewcode.evolution.benchmark import (
    BenchmarkCase,
    compare_skill_variants,
    load_benchmark_cases,
    render_markdown_report,
    score_skill_text,
)


def test_compare_skill_variants_scores_evolved_above_baseline(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.jsonl"
    rows = [
        {
            "id": "case_one",
            "source": "unit",
            "task_family": "debug",
            "task": "Handle a regression issue.",
            "required_terms": ["复现失败", "回归测试"],
            "forbidden_terms": ["跳过测试"],
        },
        {
            "id": "case_two",
            "source": "unit",
            "task_family": "tool-use",
            "task": "Recover from a tool failure.",
            "required_terms": ["读取错误", "重试一次"],
            "forbidden_terms": ["盲目重试"],
        },
    ]
    dataset.write_text(
        "\n".join(json.dumps(row, ensure_ascii=False) for row in rows) + "\n",
        encoding="utf-8",
    )

    result = compare_skill_variants(
        dataset,
        baseline_skill="复现失败。遇到工具失败时继续处理。",
        evolved_skill="复现失败，补回归测试。读取错误后修正参数并重试一次。",
    )

    assert result["summary"]["case_count"] == 2
    assert result["summary"]["evolved_required_recall"] > result["summary"][
        "baseline_required_recall"
    ]
    assert result["summary"]["delta_required_recall"] == 0.75
    assert result["cases"][0]["evolved"]["passed"]
    assert not result["cases"][1]["baseline"]["passed"]


def test_packaged_seed_dataset_has_public_benchmark_sources() -> None:
    cases = load_benchmark_cases("benchmarks/self_evolution_seed_cases.jsonl")
    sources = {case.source for case in cases}

    assert len(cases) >= 6
    assert {"SWE-bench", "AgentBench", "MBPP", "HumanEval"}.issubset(sources)
    assert all(case.required_terms for case in cases)
    assert all(case.task for case in cases)


def test_render_markdown_report_contains_delta_and_sources(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        json.dumps({
            "id": "case_one",
            "source": "SWE-bench",
            "task_family": "debug",
            "task": "Handle a regression issue.",
            "required_terms": ["复现失败"],
            "forbidden_terms": [],
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    result = compare_skill_variants(
        dataset,
        baseline_skill="",
        evolved_skill="复现失败",
    )

    report = render_markdown_report(result)

    assert "Self-Evolution Dataset Eval" in report
    assert "Delta Required Recall" in report
    assert "SWE-bench" in report


def test_render_markdown_report_explains_dataset_rationale_and_effect(
    tmp_path: Path,
) -> None:
    dataset = tmp_path / "cases.jsonl"
    dataset.write_text(
        json.dumps({
            "id": "case_one",
            "source": "AgentBench",
            "source_url": "https://github.com/THUDM/AgentBench",
            "task_family": "tool_failure_recovery",
            "task": "Handle a failed tool call.",
            "required_terms": ["读取错误"],
            "forbidden_terms": [],
        }, ensure_ascii=False)
        + "\n",
        encoding="utf-8",
    )
    result = compare_skill_variants(
        dataset,
        baseline_skill="",
        evolved_skill="读取错误",
    )

    report = render_markdown_report(result)

    assert "Dataset Selection Rationale" in report
    assert "Before/After Interpretation" in report
    assert "tool_failure_recovery" in report
    assert "https://github.com/THUDM/AgentBench" in report


def test_non_negated_forbidden_mode_allows_safety_prohibition() -> None:
    case = BenchmarkCase(
        id="negation",
        source="unit",
        task_family="safety",
        task="Keep tests mandatory.",
        required_terms=["回归测试"],
        forbidden_terms=["跳过测试"],
    )

    strict = score_skill_text(case, "回归测试。禁止跳过测试。")
    semantic = score_skill_text(
        case,
        "回归测试。禁止跳过测试。",
        forbidden_match_mode="non_negated",
    )
    unsafe = score_skill_text(
        case,
        "回归测试太慢时可以跳过测试。",
        forbidden_match_mode="non_negated",
    )

    assert not strict["passed"]
    assert semantic["passed"]
    assert not unsafe["passed"]

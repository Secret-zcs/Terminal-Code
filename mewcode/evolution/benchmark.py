"""Deterministic benchmark helpers for self-evolution experiments."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from mewcode.evolution.eval_matching import contains_forbidden_term


@dataclass(frozen=True)
class BenchmarkCase:
    id: str
    source: str
    task_family: str
    task: str
    required_terms: list[str]
    forbidden_terms: list[str]
    source_url: str = ""
    notes: str = ""

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "BenchmarkCase":
        required = _string_list(data.get("required_terms", []))
        if not required:
            raise ValueError("benchmark case required_terms cannot be empty")
        return cls(
            id=str(data["id"]).strip(),
            source=str(data["source"]).strip(),
            task_family=str(data.get("task_family", "")).strip(),
            task=str(data["task"]).strip(),
            required_terms=required,
            forbidden_terms=_string_list(data.get("forbidden_terms", [])),
            source_url=str(data.get("source_url", "")).strip(),
            notes=str(data.get("notes", "")).strip(),
        )


DEFAULT_BASELINE_SKILL = """
处理代码任务时，阅读需求并给出简洁回答。必要时运行测试。
遇到错误时，尝试继续完成任务。
"""


DEFAULT_EVOLVED_SKILL = """
处理 issue 或仓库代码任务时，先阅读相关文件并保持既有风格；优先运行或定位目标测试，
复现失败后只改必要业务代码并形成最小补丁，最后运行目标测试和回归测试并给出验证报告。
如果测试文件已经包含 fail-to-pass 用例，先修源码，不主动改测试；不要修改 changelog、egg-info、
sitecustomize、依赖锁文件或生成物来让测试通过。遇到工具失败时，读取错误，修正参数并重试一次，
记录用户反馈和 failure usage。
涉及 rewind 时先 checkpoint，用户确认后执行，且不覆盖用户修改。
函数题先明确函数规格、输入输出约束和 docstring，再补边界用例、三个测试和断言；
只改必要代码，禁止硬编码，所有关键决策需要留档。
"""


def load_benchmark_cases(path: str | Path) -> list[BenchmarkCase]:
    dataset_path = Path(path)
    cases: list[BenchmarkCase] = []
    for line_number, line in enumerate(
        dataset_path.read_text(encoding="utf-8").splitlines(),
        1,
    ):
        stripped = line.strip()
        if not stripped:
            continue
        try:
            data = json.loads(stripped)
            case = BenchmarkCase.from_dict(data)
        except (KeyError, TypeError, json.JSONDecodeError, ValueError) as exc:
            raise ValueError(f"invalid benchmark case at line {line_number}: {exc}") from exc
        cases.append(case)
    return cases


def score_skill_text(
    case: BenchmarkCase,
    skill_text: str,
    *,
    forbidden_match_mode: str = "literal",
) -> dict[str, Any]:
    normalized = skill_text.casefold()
    required_hits = [
        term for term in case.required_terms if term.casefold() in normalized
    ]
    forbidden_hits = [
        term
        for term in case.forbidden_terms
        if contains_forbidden_term(
            skill_text,
            term,
            mode=forbidden_match_mode,
        )
    ]
    missing_required = [
        term for term in case.required_terms if term not in required_hits
    ]
    required_recall = len(required_hits) / len(case.required_terms)
    return {
        "required_hits": required_hits,
        "missing_required": missing_required,
        "forbidden_hits": forbidden_hits,
        "required_recall": round(required_recall, 4),
        "passed": not missing_required and not forbidden_hits,
    }


def compare_skill_variants(
    dataset_path: str | Path,
    *,
    baseline_skill: str = DEFAULT_BASELINE_SKILL,
    evolved_skill: str = DEFAULT_EVOLVED_SKILL,
) -> dict[str, Any]:
    cases = load_benchmark_cases(dataset_path)
    case_results: list[dict[str, Any]] = []
    for case in cases:
        case_results.append({
            "id": case.id,
            "source": case.source,
            "source_url": case.source_url,
            "task_family": case.task_family,
            "task": case.task,
            "notes": case.notes,
            "required_terms": case.required_terms,
            "forbidden_terms": case.forbidden_terms,
            "baseline": score_skill_text(case, baseline_skill),
            "evolved": score_skill_text(case, evolved_skill),
        })

    baseline_total = sum(
        float(result["baseline"]["required_recall"]) for result in case_results
    )
    evolved_total = sum(
        float(result["evolved"]["required_recall"]) for result in case_results
    )
    case_count = len(case_results)
    baseline_recall = round(baseline_total / case_count, 4) if case_count else 0.0
    evolved_recall = round(evolved_total / case_count, 4) if case_count else 0.0
    return {
        "dataset": str(dataset_path),
        "summary": {
            "case_count": case_count,
            "baseline_required_recall": baseline_recall,
            "evolved_required_recall": evolved_recall,
            "delta_required_recall": round(evolved_recall - baseline_recall, 4),
            "baseline_passed": sum(
                1 for result in case_results if result["baseline"]["passed"]
            ),
            "evolved_passed": sum(
                1 for result in case_results if result["evolved"]["passed"]
            ),
        },
        "cases": case_results,
    }


def render_markdown_report(result: dict[str, Any]) -> str:
    summary = result["summary"]
    source_rationales = _source_rationales(result["cases"])
    lines = [
        "# Self-Evolution Dataset Eval",
        "",
        "## Methodology",
        "",
        "- This is a deterministic SOP coverage benchmark, not a live model benchmark.",
        "- Local JSONL cases are either public-benchmark seed cases or privacy-preserving derived task signals; raw conversations are not included.",
        "- A case passes when all required terms are covered and no forbidden terms appear in the skill SOP.",
        "- The result measures whether self-evolution made the candidate skill more testable against expected behaviors.",
        "",
        "## Dataset Selection Rationale",
        "",
        "| Source | Why It Is Included |",
        "|---|---|",
    ]
    for source, rationale in source_rationales:
        lines.append(f"| {source} | {rationale} |")
    lines.extend([
        "",
        "## Before/After Interpretation",
        "",
        "- Baseline is the pre-evolution generic coding SOP.",
        "- Evolved is the post-evolution candidate skill SOP distilled from the self-evolution design.",
        f"- The observed delta is {summary['delta_required_recall']:.2%} required-term recall across the evaluated cases.",
        "- A positive delta means the candidate SOP covers more expected guardrails; it does not prove higher live task success.",
        "",
        "## Summary",
        "",
        f"- Dataset: `{result['dataset']}`",
        f"- Cases: {summary['case_count']}",
        f"- Baseline Required Recall: {summary['baseline_required_recall']:.2%}",
        f"- Evolved Required Recall: {summary['evolved_required_recall']:.2%}",
        f"- Delta Required Recall: {summary['delta_required_recall']:.2%}",
        f"- Baseline Passed: {summary['baseline_passed']}",
        f"- Evolved Passed: {summary['evolved_passed']}",
        "",
        "## Case Results",
        "",
        "| Case | Source | Baseline Recall | Evolved Recall | Evolved Passed |",
        "|---|---|---:|---:|---:|",
    ])
    for case in result["cases"]:
        lines.append(
            "| "
            f"{case['id']} | {case['source']} | "
            f"{case['baseline']['required_recall']:.2%} | "
            f"{case['evolved']['required_recall']:.2%} | "
            f"{'yes' if case['evolved']['passed'] else 'no'} |"
        )
    lines.extend([
        "",
        "## Source References",
        "",
        "| Source | Task Family | Reference |",
        "|---|---|---|",
    ])
    seen: set[tuple[str, str, str]] = set()
    for case in result["cases"]:
        key = (case["source"], case["task_family"], case.get("source_url", ""))
        if key in seen:
            continue
        seen.add(key)
        lines.append(f"| {key[0]} | {key[1]} | {key[2] or '(local)'} |")
    lines.extend([
        "",
        "## Limitations",
        "",
        "- This does not execute a forked agent or run repository tests.",
        "- The baseline and evolved SOPs are fixed strings used for deterministic comparison.",
        "- The next stronger benchmark should replay real tasks in a sandboxed fork-agent runner.",
    ])
    return "\n".join(lines) + "\n"


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item).strip() for item in value if str(item).strip()]


def _source_rationales(cases: list[dict[str, Any]]) -> list[tuple[str, str]]:
    known = {
        "OASST1": "Human-generated multi-turn conversations provide real follow-up and correction patterns; only sanitized task signals are retained.",
        "SWE-bench": "Repository-level issue repair tests regression reproduction, patch minimality, and verification discipline.",
        "AgentBench": "Agent/task interaction scenarios test tool-failure recovery and long-horizon safety guardrails.",
        "MBPP": "Short programming tasks test whether the skill asks for specs, boundary cases, and focused tests.",
        "HumanEval": "Function-completion tasks test docstring, input/output constraints, assertions, and anti-hardcoding behavior.",
    }
    ordered_sources: list[str] = []
    for case in cases:
        source = str(case.get("source", "")).strip()
        if source and source not in ordered_sources:
            ordered_sources.append(source)
    return [
        (source, known.get(source, "Included as a public benchmark-inspired task family."))
        for source in ordered_sources
    ]

from __future__ import annotations

import json
from pathlib import Path

from mewcode.evolution.real_dataset import (
    OASST1_REVISION,
    build_source_manifest,
    derive_oasst1_cases,
)
from mewcode.evolution.benchmark import compare_skill_variants


def _rows() -> list[dict]:
    return [
        {
            "message_id": "root-1",
            "parent_id": None,
            "message_tree_id": "tree-1",
            "role": "prompter",
            "lang": "en",
            "text": "How do I fix this Python test error?",
        },
        {
            "message_id": "assistant-1",
            "parent_id": "root-1",
            "message_tree_id": "tree-1",
            "role": "assistant",
            "lang": "en",
            "text": "Read the function and run the test.",
        },
        {
            "message_id": "feedback-1",
            "parent_id": "assistant-1",
            "message_tree_id": "tree-1",
            "role": "prompter",
            "lang": "en",
            "text": "That is wrong: the test still fails. Please correct it.",
        },
        {
            "message_id": "assistant-2",
            "parent_id": "feedback-1",
            "message_tree_id": "tree-1",
            "role": "assistant",
            "lang": "en",
            "text": "I will inspect the error, patch it, and verify the regression.",
        },
        {
            "message_id": "unrelated",
            "parent_id": None,
            "message_tree_id": "tree-2",
            "role": "prompter",
            "lang": "en",
            "text": "Tell me a joke.",
        },
    ]


def test_derive_oasst1_cases_keeps_only_sanitized_task_signals() -> None:
    cases = derive_oasst1_cases(_rows(), revision=OASST1_REVISION)

    assert len(cases) == 1
    case = cases[0]
    assert case["source"] == "OASST1"
    assert case["source_revision"] == OASST1_REVISION
    assert case["task_family"] == "code-task-with-correction"
    assert case["turn_count"] == 4
    assert case["code_signal"] is True
    assert case["feedback_signal"] is True
    assert case["required_terms"]
    serialized = json.dumps(case, ensure_ascii=False)
    assert "How do I fix" not in serialized
    assert "root-1" not in serialized
    assert "message_id" not in serialized


def test_source_manifest_records_revision_and_content_hash(tmp_path: Path) -> None:
    raw = tmp_path / "oasst1-validation.json"
    raw.write_text('{"row": 1}\n', encoding="utf-8")

    manifest = build_source_manifest(
        raw,
        dataset="OpenAssistant/oasst1",
        split="validation",
        revision=OASST1_REVISION,
        row_count=1,
    )

    assert manifest["dataset"] == "OpenAssistant/oasst1"
    assert manifest["split"] == "validation"
    assert manifest["revision"] == OASST1_REVISION
    assert len(manifest["sha256"]) == 64
    assert manifest["row_count"] == 1


def test_packaged_oasst1_derived_dataset_is_sanitized() -> None:
    path = Path("benchmarks/oasst1_derived_cases.jsonl")
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    assert len(rows) == 19
    assert all(row["source"] == "OASST1" for row in rows)
    assert all(row["source_revision"] == OASST1_REVISION for row in rows)
    assert all("text" not in row and "user_id" not in row for row in rows)


def test_packaged_oasst1_cases_show_expected_sop_coverage_delta() -> None:
    result = compare_skill_variants("benchmarks/oasst1_derived_cases.jsonl")

    assert result["summary"] == {
        "case_count": 19,
        "baseline_required_recall": 0.0,
        "evolved_required_recall": 1.0,
        "delta_required_recall": 1.0,
        "baseline_passed": 0,
        "evolved_passed": 19,
    }
